"use strict";
/* PortTool page, part 4: start-up, the header, the tabs, the job strip and the polling.

   The page asks the server for its state every 1.5 s (every 0.4 s while a job runs)
   and redraws from that answer. Nothing here is computed in the browser that the
   server can compute. */

(function () {
  var IDLE_POLL = 1500;
  var BUSY_POLL = 400;
  var JOB_LABELS = {
    "load": "Loading the repo",
    "export": "Exporting the workbook",
    "dry-run": "Validating requests",
    "apply": "Applying requests",
    "rollback": "Rolling back",
    "doctor": "Checking the tools"
  };

  var app = {
    state: null,
    modelHash: undefined,   // undefined: not known yet; null: no model
    views: {},              // table name -> TableView
    defs: {},               // table name -> definition from /api/tables
    tabsBuilt: false,
    tablesLoading: false,
    activeTab: null,
    jobId: null,
    cancelling: false,
    autoLoadTried: false,
    pollTimer: null,
    ticking: false,
    tickAgain: false,
    stopped: false
  };
  PT.app = app;

  /* ---- confirm dialog */

  PT.confirm = function (title, text, okLabel) {
    var dialog = PT.el("confirm-dialog");
    var ok = PT.el("confirm-ok");
    var cancel = PT.el("confirm-cancel");
    PT.el("confirm-title").textContent = title;
    PT.el("confirm-text").textContent = text;
    ok.textContent = okLabel;
    ok.disabled = false;
    return new Promise(function (resolve) {
      var answer = false;
      function onOk() {
        ok.disabled = true; // a double click confirms once
        answer = true;
        dialog.close();
      }
      function onCancel() { dialog.close(); }
      function onClose() {
        ok.removeEventListener("click", onOk);
        cancel.removeEventListener("click", onCancel);
        dialog.removeEventListener("close", onClose);
        resolve(answer);
      }
      ok.addEventListener("click", onOk);
      cancel.addEventListener("click", onCancel);
      dialog.addEventListener("close", onClose);
      dialog.showModal();
      cancel.focus();
    });
  };

  PT.renderFindings = function (list, findings) {
    PT.clear(list);
    (findings || []).forEach(function (finding) {
      var item = PT.make("li", "finding-" + finding.severity);
      item.appendChild(PT.make("span", "code", finding.code));
      item.appendChild(document.createTextNode(" (" + finding.severity + ") " + finding.message));
      list.appendChild(item);
    });
  };

  /* ---- tabs */

  function buildTabs(tables) {
    var tabs = PT.el("tabs");
    var panels = PT.el("panels");
    var requestsPanel = PT.el("panel-requests");
    tables.forEach(function (def) {
      app.defs[def.name] = def;
      var panel = PT.make("section", "tab-panel table-panel");
      panel.id = "panel-" + def.name;
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", "tab-" + def.name);
      panel.hidden = true;
      panels.insertBefore(panel, requestsPanel);
      tabs.appendChild(tabButton(def.name, def.title));
    });
    tabs.appendChild(tabButton("requests", "Requests"));
    tabs.addEventListener("keydown", onTabKey);
    app.tabsBuilt = true;
    selectTab("ports");
  }

  function tabButton(name, title) {
    var button = PT.make("button", "tab");
    button.type = "button";
    button.id = "tab-" + name;
    button.dataset.tab = name;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "panel-" + name);
    button.setAttribute("aria-selected", "false");
    button.tabIndex = -1;
    button.appendChild(PT.make("span", null, title));
    if (name !== "requests") { button.appendChild(PT.make("span", "tab-count", "")); }
    button.addEventListener("click", function () { selectTab(name); });
    return button;
  }

  function selectTab(name) {
    app.activeTab = name;
    Array.prototype.forEach.call(PT.el("tabs").children, function (button) {
      var selected = button.dataset.tab === name;
      button.setAttribute("aria-selected", selected ? "true" : "false");
      button.tabIndex = selected ? 0 : -1;
      PT.el("panel-" + button.dataset.tab).hidden = !selected;
    });
    if (name === "requests") {
      PT.requests.onShow();
      return;
    }
    if (!app.views[name]) {
      app.views[name] = new PT.TableView(app.defs[name], PT.el("panel-" + name));
    }
    app.views[name].activate();
  }
  PT.selectTab = selectTab;

  function onTabKey(event) {
    var buttons = Array.prototype.slice.call(PT.el("tabs").children);
    var index = buttons.indexOf(document.activeElement);
    if (index < 0) { return; }
    var next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: buttons.length - 1 }[event.key];
    if (next === undefined) { return; }
    event.preventDefault();
    var button = buttons[(next + buttons.length) % buttons.length];
    selectTab(button.dataset.tab);
    button.focus();
  }

  /* Reads the table list: builds the tabs once, then keeps the row counts current. */
  function loadTables() {
    if (app.tablesLoading) { return; }
    app.tablesLoading = true;
    PT.get("/api/tables").then(
      function (info) {
        app.tablesLoading = false;
        if (!app.tabsBuilt) { buildTabs(info.tables); }
        info.tables.forEach(function (table) {
          var count = PT.el("tab-" + table.name).querySelector(".tab-count");
          count.textContent = table.rows === null ? "" : PT.fmt(table.rows);
        });
        if (info.status === "preparing") { window.setTimeout(loadTables, 300); }
      },
      function (error) {
        app.tablesLoading = false;
        PT.showError(error, "Tables");
      }
    );
  }

  function modelChanged() {
    loadTables();
    Object.keys(app.views).forEach(function (name) { app.views[name].invalidate(); });
    PT.requests.onModelChanged();
  }

  /* ---- jobs */

  function showStrip(job) {
    PT.el("job-strip").hidden = false;
    PT.el("job-name").textContent = JOB_LABELS[job.name] || job.name;
    var progress = PT.el("job-progress");
    if (job.total > 0) {
      progress.value = Math.min(100, Math.round((100 * job.done) / job.total));
    } else {
      progress.removeAttribute("value"); // unknown length: moving bar
    }
    PT.el("job-message").textContent = job.message || "";
    PT.el("job-elapsed").textContent = job.elapsed_s === null || job.elapsed_s === undefined ? "" : PT.duration(job.elapsed_s);
    var cancel = PT.el("btn-cancel-job");
    cancel.disabled = app.cancelling;
    cancel.textContent = app.cancelling ? "Cancelling…" : "Cancel";
  }

  function hideStrip() {
    PT.el("job-strip").hidden = true;
    app.cancelling = false;
  }

  function trackJob(id, job) {
    app.jobId = id;
    app.cancelling = false;
    if (job) { showStrip(job); }
    setBusy(true);
    tick();
  }

  /* Starts a job with a POST. Resolves with the answer; the caller shows errors. */
  PT.startJob = function (path, payload, isUpload) {
    var call = isUpload ? PT.upload(path, payload) : PT.post(path, payload);
    return call.then(function (answer) {
      trackJob(answer.job_id, answer.job);
      return answer;
    });
  };

  function startSimple(path, what) {
    PT.startJob(path).catch(function (error) { PT.showError(error, what); });
  }

  function summaryText(summary) {
    if (!summary) { return ""; }
    return Object.keys(summary).map(function (key) { return key + ": " + summary[key]; }).join(", ");
  }

  function jobEnded(job) {
    var label = JOB_LABELS[job.name] || job.name;
    if (job.state === "failed") {
      var text = job.error || "The job failed without a message.";
      if (job.log_file) { text += "\nLog file: " + job.log_file; }
      PT.showError({ message: text }, label + " failed");
    } else if (job.state === "cancelled") {
      PT.showInfo(label + " was cancelled.");
    } else if (job.name === "doctor" || job.name === "rollback") {
      var summary = summaryText(job.summary);
      PT.showInfo(label + " finished." + (summary ? " " + summary : ""));
    }
    PT.requests.onJobEnd(job);
  }

  function refreshJob() {
    var id = app.jobId;
    return PT.get("/api/jobs/" + encodeURIComponent(id)).then(
      function (job) {
        if (app.jobId !== id) { return; }
        if (job.active) {
          showStrip(job);
          PT.requests.onJobProgress(job);
          return;
        }
        app.jobId = null;
        hideStrip();
        jobEnded(job);
        return refreshState();
      },
      function (error) {
        if (error.code === "unknown-job") {
          app.jobId = null;
          hideStrip();
        }
        throw error;
      }
    );
  }

  /* ---- state */

  function setBusy(busy) {
    var state = app.state || {};
    PT.el("btn-reload").disabled = busy || !state.config_present || !!state.recovery_needed;
    PT.el("btn-reload-stale").disabled = busy;
    PT.el("btn-doctor").disabled = busy || !state.config_present;
    PT.el("btn-rollback").disabled = busy;
    PT.el("btn-open").disabled = busy;
  }

  function countsText(model) {
    if (!model) { return "not loaded"; }
    var c = model.counts;
    return [
      PT.fmt(c.components) + " SWCs",
      PT.fmt(c.ports) + " ports",
      PT.fmt(c.connectors) + " connections",
      PT.fmt(c.interfaces) + " interfaces",
      PT.fmt(c.data_types) + " data types"
    ].join(" · ");
  }

  function applyState(state) {
    app.state = state;
    var path = PT.el("repo-path");
    path.textContent = state.repo || "No repo open";
    path.title = state.repo || "";
    PT.el("model-counts").textContent = state.config_present ? countsText(state.model) : "";

    PT.el("view-start").hidden = true;
    PT.el("view-setup").hidden = state.config_present;
    PT.el("view-main").hidden = !state.config_present;
    PT.el("banner-recovery").hidden = !state.recovery_needed;
    PT.el("banner-stale").hidden = !state.stale || state.recovery_needed;

    var running = state.jobs.length ? state.jobs[0] : null;
    if (running && !app.jobId) {
      app.jobId = running.id; // a job this page did not start (command line, other tab)
      app.cancelling = false;
    }
    if (running && running.id === app.jobId) { showStrip(running); }
    var busy = app.jobId !== null;
    setBusy(busy);

    if (!app.tabsBuilt && state.config_present) { loadTables(); }
    var hash = state.model ? state.model.hash : null;
    if (hash !== app.modelHash) {
      app.modelHash = hash;
      if (state.config_present) { modelChanged(); }
    }

    if (state.config_present && !state.model_loaded && !busy && !state.recovery_needed && !app.autoLoadTried) {
      app.autoLoadTried = true; // once: a failed load is not repeated in a loop
      startSimple("/api/load", "Load");
    }
    PT.requests.onState(state);
  }

  function refreshState() {
    return PT.get("/api/state").then(applyState);
  }

  /* One round: state, then the running job. Only one round runs at a time; a call
     that arrives meanwhile makes the next round start at once. */
  function tick() {
    if (app.stopped) { return; }
    if (app.ticking) {
      app.tickAgain = true;
      return;
    }
    app.ticking = true;
    app.tickAgain = false;
    if (app.pollTimer) { window.clearTimeout(app.pollTimer); app.pollTimer = null; }
    refreshState()
      .then(function () { return app.jobId ? refreshJob() : null; })
      .catch(function (error) {
        if (error.code === "bad-token" || error.code === "bad-host") {
          app.stopped = true;
          PT.el("view-start").hidden = false;
          PT.el("view-setup").hidden = true;
          PT.el("view-main").hidden = true;
          PT.el("start-text").textContent =
            "This page is no longer connected to PortTool: " + PT.errorText(error);
          return;
        }
        PT.showError(error, "State");
      })
      .then(function () {
        app.ticking = false;
        if (app.stopped) { return; }
        if (app.tickAgain) {
          tick();
        } else {
          app.pollTimer = window.setTimeout(tick, app.jobId ? BUSY_POLL : IDLE_POLL);
        }
      });
  }
  PT.refreshNow = tick;

  /* ---- events */

  function bind() {
    PT.el("btn-reload").addEventListener("click", function () { startSimple("/api/load", "Reload"); });
    PT.el("btn-reload-stale").addEventListener("click", function () { startSimple("/api/load", "Reload"); });
    PT.el("btn-doctor").addEventListener("click", function () { startSimple("/api/doctor", "Check tools"); });
    PT.el("btn-rollback").addEventListener("click", function () {
      PT.confirm(
        "Roll back?",
        "PortTool restores every file the unfinished apply changed and removes its journal.",
        "Roll back"
      ).then(function (yes) {
        if (yes) { startSimple("/api/rollback", "Roll back"); }
      });
    });
    PT.el("btn-cancel-job").addEventListener("click", function () {
      if (!app.jobId || app.cancelling) { return; }
      app.cancelling = true;
      var button = PT.el("btn-cancel-job");
      button.disabled = true;
      button.textContent = "Cancelling…";
      PT.post("/api/jobs/" + encodeURIComponent(app.jobId) + "/cancel").catch(function (error) {
        app.cancelling = false;
        PT.showError(error, "Cancel");
      });
    });
    PT.el("setup-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var repo = PT.el("repo-input").value.trim();
      PT.renderFindings(PT.el("setup-findings"), []);
      PT.post("/api/open-repo", { repo: repo }).then(
        function (answer) {
          PT.renderFindings(PT.el("setup-findings"), answer.findings);
          answer.findings.forEach(function (finding) {
            if (finding.severity !== "info") {
              PT.showInfo("Configuration " + finding.severity + " " + finding.code + ": " + finding.message);
            }
          });
          app.autoLoadTried = false;
          applyState(answer.state);
        },
        function (error) { PT.showError(error, "Open repo"); }
      );
    });
    document.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "f") {
        var view = app.views[app.activeTab];
        if (view && !PT.el("view-main").hidden) {
          event.preventDefault();
          view.focusSearch();
        }
      }
    });
  }

  function start() {
    if (!PT.initToken()) {
      PT.el("start-text").textContent =
        "This page was opened without its session token, so PortTool will not answer it. " +
        "Open the address that 'porttool gui' printed (it ends with ?token=...). " +
        "Reloading the page also drops the token: use the printed address again.";
      return;
    }
    bind();
    PT.requests.init();
    tick();
  }

  window.addEventListener("error", function (event) { PT.fatal(event.error || event.message); });
  window.addEventListener("unhandledrejection", function (event) {
    PT.showError(event.reason, "Page error");
  });

  function safeStart() {
    try {
      start();
    } catch (error) {
      PT.fatal(error);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", safeStart);
  } else {
    safeStart();
  }
})();
