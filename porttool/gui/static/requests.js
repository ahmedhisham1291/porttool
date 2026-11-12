"use strict";
/* PortTool page, part 3: the Requests tab.
   (1) export a workbook, (2) import workbooks, (3) the request form and its list,
   (4) the results of the last validation, (5) apply. */

(function () {
  var MAX_OPTIONS = 3000; // suggestions per text field; longer lists are cut (typing still works)

  var R = {
    state: null,
    busy: false,
    files: [],          // File objects chosen for the import
    schema: null,       // answer of /api/form
    kinds: {},          // kind name -> kind of the schema
    lists: {},          // answer of /api/lists
    listsLoading: false,
    pending: [],        // requests of the form: {kind, values, children, requester, comment}
    editing: null,      // index in pending while a request is being edited
    statusFilter: null,
    validationGen: 0,
    exportTouched: false,
    applyJobId: null,
    confirming: false,
    reportLoaded: false
  };

  /* ================================================================ (1) export */

  function initExport() {
    var target = PT.el("export-target");
    target.addEventListener("input", function () { R.exportTouched = true; });
    PT.el("export-form").addEventListener("submit", function (event) {
      event.preventDefault();
      PT.el("export-result").hidden = true;
      PT.startJob("/api/export", { target: target.value.trim() }).catch(function (error) {
        PT.showError(error, "Export");
      });
    });
  }

  /* ================================================================ (2) import */

  function fileSize(bytes) {
    return bytes >= 1024 * 1024
      ? (bytes / (1024 * 1024)).toFixed(1) + " MB"
      : Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  function addFiles(fileList) {
    var limitMb = (R.state && R.state.max_upload_mb) || 50;
    var limitFiles = (R.state && R.state.max_upload_files) || 20;
    Array.prototype.forEach.call(fileList, function (file) {
      if (!/\.xlsx$/i.test(file.name)) {
        PT.showError({ message: "'" + file.name + "' is not an .xlsx workbook." }, "Import");
        return;
      }
      var known = R.files.some(function (other) {
        return other.name === file.name && other.size === file.size;
      });
      if (known) { return; }
      var total = R.files.reduce(function (sum, other) { return sum + other.size; }, file.size);
      if (total > limitMb * 1024 * 1024) {
        PT.showError(
          { message: "'" + file.name + "' does not fit: one import may hold " + limitMb + " MB in total." },
          "Import"
        );
        return;
      }
      if (R.files.length >= limitFiles) {
        PT.showError({ message: "One import may hold " + limitFiles + " workbooks." }, "Import");
        return;
      }
      R.files.push(file);
    });
    renderFiles();
  }

  function renderFiles() {
    var list = PT.el("file-list");
    PT.clear(list);
    R.files.forEach(function (file, index) {
      var item = PT.make("li");
      var name = PT.make("span", null, file.name + " (" + fileSize(file.size) + ")");
      name.dir = "auto";
      item.appendChild(name);
      var remove = PT.make("button", "btn btn-small", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + file.name);
      remove.addEventListener("click", function () {
        R.files.splice(index, 1);
        renderFiles();
      });
      item.appendChild(remove);
      list.appendChild(item);
    });
    updateButtons();
  }

  function initImport() {
    var zone = PT.el("drop-zone");
    var input = PT.el("file-input");
    input.addEventListener("change", function () {
      addFiles(input.files);
      input.value = ""; // choosing the same file again must fire 'change' again
    });
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("over");
      });
    });
    zone.addEventListener("dragleave", function () { zone.classList.remove("over"); });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      zone.classList.remove("over");
      if (event.dataTransfer && event.dataTransfer.files) { addFiles(event.dataTransfer.files); }
    });
    PT.el("btn-validate-files").addEventListener("click", function () {
      var data = new FormData();
      R.files.forEach(function (file) { data.append("file", file, file.name); });
      PT.startJob("/api/dry-run/upload", data, true).catch(function (error) {
        PT.showError(error, "Import");
      });
    });
  }

  /* ================================================================ (3) request form */

  function currentKind() {
    return R.kinds[PT.el("form-kind").value];
  }

  function datalistId(name) { return "dl-" + name; }

  /* One shared <datalist> per list of the server (SWCs, interfaces, ...). */
  function fillDatalist(id, values) {
    var list = document.getElementById(id);
    if (!list) {
      list = PT.make("datalist");
      list.id = id;
      PT.el("request-form").appendChild(list);
    }
    PT.clear(list);
    (values || []).slice(0, MAX_OPTIONS).forEach(function (value) {
      var option = PT.make("option");
      option.value = value;
      list.appendChild(option);
    });
  }

  function refreshDatalists() {
    Object.keys(R.lists).forEach(function (name) { fillDatalist(datalistId(name), R.lists[name]); });
  }

  /* Builds the control of one field. 'prefix' keeps the ids unique. */
  function buildControl(field, prefix) {
    var control;
    if (field.list && field.closed) {
      control = PT.make("select");
      var none = PT.make("option", null, "");
      none.value = "";
      control.appendChild(none);
      (R.lists[field.list] || []).forEach(function (value) {
        var option = PT.make("option", null, value);
        option.value = value;
        control.appendChild(option);
      });
    } else {
      control = PT.make("input");
      control.type = "text";
      control.setAttribute("autocomplete", "off");
      control.setAttribute("spellcheck", "false");
      control.dir = "auto";
      if (field.list) {
        control.setAttribute("list", datalistId(field.list));
      } else if (field.suggest) {
        control.setAttribute("list", datalistId("suggest-" + prefix + field.key));
      }
    }
    control.id = prefix + field.key;
    control.dataset.key = field.key;
    return control;
  }

  function buildField(field, prefix) {
    var wrap = PT.make("div", "field");
    var label = PT.make("label", null, field.label);
    label.htmlFor = prefix + field.key;
    if (field.required) {
      var mark = PT.make("span", "required-mark", " *");
      mark.title = "required";
      label.appendChild(mark);
    }
    wrap.appendChild(label);
    wrap.appendChild(buildControl(field, prefix));
    if (field.hint) { wrap.appendChild(PT.make("p", "hint", field.hint)); }
    return wrap;
  }

  /* A field with 'suggest' offers the names of the SWC or prototype chosen in another field. */
  function wireSuggestions(kind) {
    kind.fields.forEach(function (field) {
      if (!field.suggest) { return; }
      var owner = document.getElementById("f-" + field.suggest.owner_field);
      if (!owner) { return; }
      var listId = datalistId("suggest-f-" + field.key);
      function update() {
        var name = owner.value.trim();
        if (!name) { fillDatalist(listId, []); return; }
        PT.get("/api/lists?" + field.suggest.owner + "=" + encodeURIComponent(name)).then(
          function (answer) {
            if (owner.value.trim() !== name) { return; } // the owner changed meanwhile
            var details = answer[field.suggest.owner] || {};
            fillDatalist(listId, details[field.suggest.names] || []);
          },
          function (error) { PT.showError(error, "Suggestions"); }
        );
      }
      owner.addEventListener("change", update);
      update();
    });
  }

  function renderForm(values, children) {
    var kind = currentKind();
    var box = PT.el("form-fields");
    var keep = {};
    Array.prototype.forEach.call(box.querySelectorAll("[data-key]"), function (control) {
      keep[control.dataset.key] = control.value;
    });
    PT.clear(box);
    if (!kind) { return; }
    kind.fields.forEach(function (field) { box.appendChild(buildField(field, "f-")); });
    R.schema.common.forEach(function (field) { box.appendChild(buildField(field, "f-")); });
    var given = values || { requester: keep.requester || "" };
    Object.keys(given).forEach(function (key) {
      var control = document.getElementById("f-" + key);
      if (control) { control.value = given[key]; }
    });
    wireSuggestions(kind);

    var childBox = PT.el("form-children");
    PT.clear(PT.el("children-rows"));
    childBox.hidden = !kind.children;
    if (kind.children) {
      PT.el("children-title").textContent = kind.children.title;
      (children && children.length ? children : [{}]).forEach(addChildRow);
    }
    PT.el("form-error").hidden = true;
  }

  function addChildRow(values) {
    var kind = currentKind();
    var rows = PT.el("children-rows");
    var number = rows.children.length + 1;
    var row = PT.make("div", "child-row");
    kind.children.fields.forEach(function (field) {
      var wrap = buildField(field, "c" + number + "-" + Date.now() + "-");
      var control = wrap.querySelector("[data-key]");
      if (values && values[field.key] !== undefined) { control.value = values[field.key]; }
      row.appendChild(wrap);
    });
    var remove = PT.make("button", "btn btn-small", "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove this element row");
    remove.addEventListener("click", function () { row.remove(); });
    row.appendChild(remove);
    rows.appendChild(row);
  }

  function readControls(container) {
    var values = {};
    Array.prototype.forEach.call(container.querySelectorAll("[data-key]"), function (control) {
      values[control.dataset.key] = control.value.trim();
    });
    return values;
  }

  function missing(fields, values) {
    return fields
      .filter(function (field) { return field.required && !values[field.key]; })
      .map(function (field) { return field.label; });
  }

  function formError(text) {
    var box = PT.el("form-error");
    box.textContent = text;
    box.hidden = !text;
  }

  function addRequest() {
    var kind = currentKind();
    if (!kind) { return; }
    var values = readControls(PT.el("form-fields"));
    var requester = values.requester || "";
    var comment = values.comment || "";
    delete values.requester;
    delete values.comment;
    var lacking = missing(kind.fields, values);
    if (lacking.length) {
      formError("Fill in: " + lacking.join(", ") + ".");
      return;
    }
    var children = [];
    if (kind.children) {
      var bad = 0;
      Array.prototype.forEach.call(PT.el("children-rows").children, function (row) {
        var child = readControls(row);
        var filled = Object.keys(child).some(function (key) { return child[key] !== ""; });
        if (!filled) { return; }
        if (missing(kind.children.fields, child).length) { bad += 1; }
        children.push(child);
      });
      if (bad) {
        formError("Every element row needs: " + missing(kind.children.fields, {}).join(", ") + ".");
        return;
      }
    }
    formError("");
    var request = { kind: kind.kind, values: values, children: children, requester: requester, comment: comment };
    if (R.editing !== null) {
      R.pending[R.editing] = request;
    } else {
      R.pending.push(request);
    }
    stopEditing();
    renderForm({ requester: requester, swc: values.swc || "" });
    renderPending();
  }

  function stopEditing() {
    R.editing = null;
    PT.el("btn-add-request").textContent = "Add to list";
    PT.el("btn-cancel-edit").hidden = true;
  }

  function editRequest(index) {
    var request = R.pending[index];
    R.editing = index;
    PT.el("form-kind").value = request.kind;
    var values = Object.assign({}, request.values, { requester: request.requester, comment: request.comment });
    renderForm(values, request.children);
    PT.el("btn-add-request").textContent = "Save changes";
    PT.el("btn-cancel-edit").hidden = false;
    PT.el("form-kind").focus();
  }

  function summary(request) {
    var kind = R.kinds[request.kind];
    var parts = kind.fields
      .map(function (field) { return request.values[field.key]; })
      .filter(function (value) { return value; });
    var text = parts.slice(0, 6).join(" · ");
    if (request.children.length) { text += " (+" + request.children.length + " element rows)"; }
    return text;
  }

  function renderPending() {
    var table = PT.el("form-list");
    var body = table.querySelector("tbody");
    PT.clear(body);
    R.pending.forEach(function (request, index) {
      var row = PT.make("tr");
      row.appendChild(PT.make("td", "nowrap", index + 1));
      row.appendChild(PT.make("td", "nowrap", R.kinds[request.kind].title));
      var text = PT.make("td", null, summary(request));
      text.dir = "auto";
      row.appendChild(text);
      row.appendChild(PT.make("td", null, request.requester));
      var actions = PT.make("td", "nowrap");
      var edit = PT.make("button", "btn btn-small", "Edit");
      edit.type = "button";
      edit.setAttribute("aria-label", "Edit request " + (index + 1));
      edit.addEventListener("click", function () { editRequest(index); });
      var remove = PT.make("button", "btn btn-small", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove request " + (index + 1));
      remove.addEventListener("click", function () {
        R.pending.splice(index, 1);
        if (R.editing !== null) { stopEditing(); renderForm(); }
        renderPending();
      });
      actions.appendChild(edit);
      actions.appendChild(document.createTextNode(" "));
      actions.appendChild(remove);
      row.appendChild(actions);
      body.appendChild(row);
    });
    table.hidden = R.pending.length === 0;
    PT.el("form-list-empty").hidden = R.pending.length !== 0;
    PT.el("form-count").textContent = R.pending.length ? "(" + R.pending.length + ")" : "";
    updateButtons();
  }

  /* The list as rows of the request sheets: what POST /api/dry-run/rows expects. */
  function pendingRows() {
    var rows = [];
    R.pending.forEach(function (request) {
      var kind = R.kinds[request.kind];
      rows.push({
        sheet: kind.sheet,
        action: R.schema.action,
        values: request.values,
        requester: request.requester,
        comment: request.comment
      });
      request.children.forEach(function (child) {
        var values = Object.assign({}, child);
        values[kind.children.link_field] = request.values[kind.children.link_from];
        rows.push({
          sheet: kind.children.sheet,
          action: R.schema.action,
          values: values,
          requester: request.requester,
          comment: request.comment
        });
      });
    });
    return rows;
  }

  function loadLists() {
    if (R.listsLoading) { return; }
    R.listsLoading = true;
    PT.get("/api/lists").then(
      function (answer) {
        R.listsLoading = false;
        if (answer.status === "preparing") {
          window.setTimeout(loadLists, 500);
          return;
        }
        R.lists = answer.lists || {};
        refreshDatalists();
        if (R.schema && R.editing === null) { renderForm(); }
      },
      function (error) {
        R.listsLoading = false;
        PT.showError(error, "Lists");
      }
    );
  }

  function initForm() {
    var select = PT.el("form-kind");
    select.addEventListener("change", function () {
      stopEditing();
      renderForm({});
    });
    PT.el("request-form").addEventListener("submit", function (event) {
      event.preventDefault();
      addRequest();
    });
    PT.el("btn-add-child").addEventListener("click", function () { addChildRow({}); });
    PT.el("btn-cancel-edit").addEventListener("click", function () {
      stopEditing();
      renderForm({});
    });
    PT.el("btn-validate-form").addEventListener("click", function () {
      PT.startJob("/api/dry-run/rows", { rows: pendingRows() }).catch(function (error) {
        PT.showError(error, "Validate");
      });
    });
    PT.get("/api/form").then(
      function (schema) {
        R.schema = schema;
        schema.kinds.forEach(function (kind) {
          R.kinds[kind.kind] = kind;
          var option = PT.make("option", null, kind.title);
          option.value = kind.kind;
          select.appendChild(option);
        });
        renderForm({});
      },
      function (error) { PT.showError(error, "Request form"); }
    );
  }

  /* ================================================================ (4) results */

  function badge(status) {
    return PT.make("span", "badge badge-" + status.replace(/ /g, "-"), status);
  }

  function chip(label, status) {
    var button = PT.make("button", "chip", label);
    button.type = "button";
    button.dataset.status = status || "";
    button.setAttribute("aria-pressed", R.statusFilter === status ? "true" : "false");
    button.addEventListener("click", function () {
      R.statusFilter = status;
      loadValidation();
    });
    return button;
  }

  function sourceText(row) {
    if (!row) { return ""; }
    return row.file + " · " + row.sheet + " · row " + row.row;
  }

  function renderValidation(data) {
    PT.el("results-empty").hidden = data.present;
    PT.el("results").hidden = !data.present;
    if (!data.present) { return; }

    var chips = PT.el("status-filter");
    PT.clear(chips);
    chips.appendChild(chip("All (" + PT.fmt(data.total) + ")", null));
    Object.keys(data.counts).forEach(function (status) {
      chips.appendChild(chip(status + " (" + PT.fmt(data.counts[status]) + ")", status));
    });

    var body = PT.el("results-table").querySelector("tbody");
    PT.clear(body);
    data.results.forEach(function (result) {
      var row = PT.make("tr");
      row.dataset.status = result.status;
      var status = PT.make("td", "nowrap");
      status.appendChild(badge(result.status));
      row.appendChild(status);
      row.appendChild(PT.make("td", "nowrap code", result.request_id));
      var source = PT.make("td", null, sourceText(result.row));
      source.dir = "auto";
      row.appendChild(source);
      var reasons = PT.make("td");
      if (result.reasons.length) {
        var list = PT.make("ul", "reasons");
        result.reasons.forEach(function (reason) {
          var item = PT.make("li", "finding-" + reason.severity);
          item.appendChild(PT.make("span", "code", reason.code));
          item.appendChild(document.createTextNode(" " + reason.message));
          list.appendChild(item);
        });
        reasons.appendChild(list);
      }
      row.appendChild(reasons);
      body.appendChild(row);
    });

    var note = PT.el("results-note");
    note.hidden = !data.truncated;
    note.textContent = data.truncated
      ? "Only the first " + PT.fmt(data.results.length) + " requests are listed. Use the status filter."
      : "";

    PT.renderFindings(PT.el("validation-findings"), data.findings);
    if (!data.findings.length) {
      PT.el("validation-findings").appendChild(PT.make("li", "hint", "None."));
    }
    var files = PT.el("plan-files");
    PT.clear(files);
    var preview = PT.el("plan-preview");
    if (data.plan) {
      data.plan.files.forEach(function (file) {
        var item = PT.make("li");
        item.appendChild(PT.make("span", "code", file.rel_path));
        item.appendChild(document.createTextNode(" (" + file.action + ") " + file.summary));
        files.appendChild(item);
      });
      (data.plan.findings || []).forEach(function (finding) {
        files.appendChild(PT.make("li", "finding-" + finding.severity, finding.code + " " + finding.message));
      });
      preview.textContent = data.plan.preview.length
        ? data.plan.preview.join("\n") + (data.plan.preview_truncated ? "\n…" : "")
        : "Nothing would be written.";
    } else {
      preview.textContent = "No plan is available.";
    }
    if (!files.children.length) { files.appendChild(PT.make("li", "hint", "No file would change.")); }
  }

  function loadValidation() {
    R.validationGen += 1;
    var gen = R.validationGen;
    var query = R.statusFilter ? "?status=" + encodeURIComponent(R.statusFilter) : "";
    return PT.get("/api/validation" + query).then(
      function (data) {
        if (gen !== R.validationGen) { return; }
        if (R.statusFilter && data.present && !data.counts[R.statusFilter]) {
          R.statusFilter = null; // the status no longer exists in this dry run: show all
          return loadValidation();
        }
        renderValidation(data);
      },
      function (error) { PT.showError(error, "Validation results"); }
    );
  }

  /* ================================================================ (5) apply */

  function showResult(id, text, bad, details) {
    var box = PT.el(id);
    PT.clear(box);
    box.hidden = false;
    box.classList.toggle("bad", !!bad);
    box.appendChild(PT.make("strong", null, text));
    if (details && details.length) {
      var list = PT.make("dl");
      details.forEach(function (pair) {
        list.appendChild(PT.make("dt", null, pair[0]));
        var value = PT.make("dd", pair[2] ? "code" : null, pair[1]);
        value.dir = "auto";
        list.appendChild(value);
      });
      box.appendChild(list);
    }
  }

  function loadReport() {
    return PT.get("/api/report").then(
      function (report) {
        R.reportLoaded = true;
        if (!report.present) { return; }
        var counts = Object.keys(report.counts)
          .map(function (status) { return report.counts[status] + " " + status; })
          .join(", ");
        var details = [];
        if (report.branch) { details.push(["Git branch", report.branch, true]); }
        if (report.report_file) { details.push(["Report", report.report_file, true]); }
        if (counts) { details.push(["Requests", counts, false]); }
        report.findings.forEach(function (finding) {
          details.push([finding.code, finding.message, false]);
        });
        var title = report.rolled_back
          ? "The changes were rolled back."
          : report.ok ? "Apply finished." : "Apply finished with problems.";
        showResult("apply-result", title, !report.ok || report.rolled_back, details);
      },
      function (error) { PT.showError(error, "Apply report"); }
    );
  }

  function showLog(job) {
    PT.el("apply-log-box").hidden = false;
    var log = PT.el("apply-log");
    log.textContent = (job.log_tail || []).join("\n");
    log.scrollTop = log.scrollHeight;
  }

  function initApply() {
    PT.el("btn-apply").addEventListener("click", function () {
      if (R.confirming || !R.state) { return; }
      R.confirming = true;
      updateButtons();
      var count = R.state.validation.accepted;
      PT.confirm(
        "Apply " + count + (count === 1 ? " request?" : " requests?"),
        "PortTool will apply " + count + " accepted " + (count === 1 ? "request" : "requests") +
          " to " + R.state.repo + ". A new git branch will be created and the workspace ARXML " +
          "files are edited on that branch.",
        "Apply"
      ).then(function (yes) {
        if (!yes) {
          R.confirming = false;
          updateButtons();
          return;
        }
        PT.el("apply-result").hidden = true;
        PT.el("apply-log").textContent = "";
        PT.startJob("/api/apply").then(
          function (answer) {
            R.applyJobId = answer.job_id;
            R.confirming = false;
            if (answer.job) { showLog(answer.job); }
            updateButtons();
          },
          function (error) {
            R.confirming = false;
            updateButtons();
            PT.showError(error, "Apply");
          }
        );
      });
    });
  }

  /* ================================================================ wiring */

  function updateButtons() {
    var state = R.state;
    if (!state) { return; }
    var blocked = R.busy || !state.model_loaded || state.recovery_needed;
    PT.el("btn-export").disabled = blocked;
    PT.el("btn-validate-files").disabled = blocked || R.files.length === 0;
    PT.el("btn-validate-form").disabled = blocked || R.pending.length === 0;
    PT.el("btn-apply").disabled = R.busy || R.confirming || !state.validation.can_apply;
    var blockers = state.validation.blockers;
    var accepted = state.validation.accepted;
    PT.el("apply-blockers").textContent = blockers.length
      ? blockers.join(" ")
      : accepted + (accepted === 1 ? " accepted request is" : " accepted requests are") + " ready to be applied.";
  }

  PT.requests = {
    init: function () {
      initExport();
      initImport();
      initForm();
      initApply();
      renderPending();
    },

    onState: function (state) {
      var first = R.state === null;
      R.state = state;
      R.busy = PT.app.jobId !== null || state.jobs.length > 0;
      var target = PT.el("export-target");
      if (!R.exportTouched && state.default_export_target && target.value !== state.default_export_target) {
        target.value = state.default_export_target;
      }
      if (first && state.validation.present) { loadValidation(); }
      if (!R.reportLoaded && state.report_present) { loadReport(); }
      updateButtons();
    },

    onShow: function () {},

    onModelChanged: function () {
      loadLists();
      loadValidation();
    },

    onJobProgress: function (job) {
      if (job.name === "apply") { showLog(job); }
    },

    onJobEnd: function (job) {
      R.busy = false;
      if (job.name === "export" && job.state === "done") {
        var target = job.summary && job.summary.target ? job.summary.target : PT.el("export-target").value;
        showResult("export-result", "Workbook written.", false, [["File", target, true]]);
      }
      if (job.name === "dry-run" && job.state === "done") {
        R.statusFilter = null;
        PT.selectTab("requests");
        loadValidation().then(function () {
          PT.el("h-results").scrollIntoView({ block: "start" });
        });
      }
      if (job.name === "apply") {
        showLog(job);
        R.applyJobId = null;
        if (job.state === "done") {
          loadReport();
        } else if (job.state === "failed") {
          var details = job.log_file ? [["Log file", job.log_file, true]] : [];
          showResult("apply-result", "Apply failed: " + (job.error || "no message"), true, details);
        } else {
          showResult("apply-result", "Apply was cancelled.", true, []);
        }
      }
      if (job.name === "rollback" && job.state === "done") { loadReport(); }
    }
  };
})();
