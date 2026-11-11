"use strict";
/* PortTool page, part 2: one table with server-side filter, sort and paging.

   Only the visible rows exist in the page. The body has the height of all filtered rows;
   rows are drawn at their place while the user scrolls, and pages of 200 rows are
   fetched when they are needed. Every query has a number (gen): an answer that belongs
   to an older query is ignored. */

(function () {
  var ROW_H = 28;        // must match --row-h in app.css
  var PAGE = 200;        // rows per fetch
  var OVERSCAN = 6;      // extra rows drawn above and below the visible part
  var MAX_PAGES = 40;    // pages kept in memory per table
  var TYPE_DELAY = 250;  // ms to wait after the last key press
  var FETCH_DELAY = 60;  // ms to wait after the last scroll step

  function TableView(def, panel) {
    this.def = def;
    this.panel = panel;
    this.gen = 0;
    this.pages = new Map();
    this.inflight = new Set();
    this.failed = false;
    this.dirty = true;
    this.sort = null;
    this.dir = "asc";
    this.q = "";
    this.filters = {};
    this.only = false;
    this.total = null;
    this.filtered = 0;
    this.note = "";
    this.typeTimer = null;
    this.fetchTimer = null;
    this.frame = null;
    this.build();
  }

  TableView.prototype.build = function () {
    var self = this;
    var def = this.def;

    var toolbar = PT.make("div", "table-toolbar");
    var searchLabel = PT.make("label", null, "Search");
    this.search = PT.make("input", "search");
    this.search.type = "search";
    this.search.id = "search-" + def.name;
    this.search.setAttribute("autocomplete", "off");
    this.search.setAttribute("spellcheck", "false");
    this.search.placeholder = "all columns";
    searchLabel.appendChild(this.search);
    toolbar.appendChild(searchLabel);
    this.search.addEventListener("input", function () {
      self.q = self.search.value;
      self.queryChanged(true);
    });

    this.onlyBox = null;
    if (def.only && def.only.indexOf("unconnected") >= 0) {
      var onlyLabel = PT.make("label");
      this.onlyBox = PT.make("input");
      this.onlyBox.type = "checkbox";
      onlyLabel.appendChild(this.onlyBox);
      onlyLabel.appendChild(document.createTextNode("Only unconnected"));
      toolbar.appendChild(onlyLabel);
      this.onlyBox.addEventListener("change", function () {
        self.only = self.onlyBox.checked;
        self.queryChanged(false);
      });
    }

    var clear = PT.make("button", "btn btn-small", "Clear filters");
    clear.type = "button";
    clear.addEventListener("click", function () { self.clearFilters(); });
    toolbar.appendChild(clear);

    this.count = PT.make("span", "table-count");
    this.count.setAttribute("role", "status");
    toolbar.appendChild(this.count);
    this.panel.appendChild(toolbar);

    this.scroller = PT.make("div", "vt-scroll");
    this.scroller.tabIndex = 0;
    this.scroller.setAttribute("role", "grid");
    this.scroller.setAttribute("aria-label", def.title + " table");
    var widths = def.columns.map(function (column, index) {
      var px = column.width + "px";
      return index === def.columns.length - 1 ? "minmax(" + px + ", 1fr)" : px;
    });
    var tableWidth = def.columns.reduce(function (sum, column) { return sum + column.width; }, 0);
    this.scroller.style.setProperty("--cols", widths.join(" "));
    this.scroller.style.setProperty("--table-w", tableWidth + "px");

    var head = PT.make("div", "vt-head");
    var headerRow = PT.make("div", "vt-row");
    headerRow.setAttribute("role", "row");
    var filterRow = PT.make("div", "vt-row");
    this.headers = {};
    this.filterInputs = {};
    def.columns.forEach(function (column) {
      var cell = PT.make("div");
      cell.setAttribute("role", "columnheader");
      cell.setAttribute("aria-sort", "none");
      var button = PT.make("button", "vt-th");
      button.type = "button";
      button.title = "Sort by " + column.header;
      button.appendChild(PT.make("span", null, column.header));
      var mark = PT.make("span", "sort-mark", "");
      mark.setAttribute("aria-hidden", "true");
      button.appendChild(mark);
      button.addEventListener("click", function () { self.toggleSort(column.key); });
      cell.appendChild(button);
      headerRow.appendChild(cell);
      self.headers[column.key] = { cell: cell, mark: mark };

      var filterCell = PT.make("div", "vt-filter");
      var input = PT.make("input");
      input.type = "text";
      input.setAttribute("aria-label", "Filter " + column.header);
      input.setAttribute("autocomplete", "off");
      input.setAttribute("spellcheck", "false");
      input.placeholder = "filter";
      input.dataset.column = column.key;
      input.addEventListener("input", function () {
        self.filters[column.key] = input.value;
        self.queryChanged(true);
      });
      filterCell.appendChild(input);
      filterRow.appendChild(filterCell);
      self.filterInputs[column.key] = input;
    });
    head.appendChild(headerRow);
    head.appendChild(filterRow);
    this.head = head;
    this.scroller.appendChild(head);

    this.body = PT.make("div", "vt-body");
    this.windowEl = PT.make("div", "vt-window");
    this.body.appendChild(this.windowEl);
    this.scroller.appendChild(this.body);
    this.empty = PT.make("div", "vt-empty");
    this.empty.hidden = true;
    this.scroller.appendChild(this.empty);
    this.panel.appendChild(this.scroller);

    this.scroller.addEventListener("scroll", function () { self.scheduleRender(); });
    this.scroller.addEventListener("keydown", function (event) { self.onKey(event); });
    window.addEventListener("resize", function () { self.scheduleRender(); });
  };

  /* ---- query */

  TableView.prototype.queryString = function (offset) {
    var params = new URLSearchParams();
    params.set("offset", String(offset));
    params.set("limit", String(PAGE));
    if (this.sort) {
      params.set("sort", this.sort);
      params.set("dir", this.dir);
    }
    if (this.q.trim()) { params.set("q", this.q.trim()); }
    var self = this;
    Object.keys(this.filters).forEach(function (key) {
      var text = self.filters[key].trim();
      if (text) { params.set("f." + key, text); }
    });
    if (this.only) { params.set("only", "unconnected"); }
    return params.toString();
  };

  /* Called when the search, a filter, the sort or the checkbox changed. */
  TableView.prototype.queryChanged = function (typed) {
    var self = this;
    if (this.typeTimer) { window.clearTimeout(this.typeTimer); this.typeTimer = null; }
    if (typed) {
      this.typeTimer = window.setTimeout(function () {
        self.typeTimer = null;
        self.reload();
      }, TYPE_DELAY);
    } else {
      this.reload();
    }
  };

  TableView.prototype.toggleSort = function (key) {
    if (this.sort !== key) {
      this.sort = key;
      this.dir = "asc";
    } else if (this.dir === "asc") {
      this.dir = "desc";
    } else {
      this.sort = null;
      this.dir = "asc";
    }
    var self = this;
    Object.keys(this.headers).forEach(function (column) {
      var active = self.sort === column;
      var header = self.headers[column];
      header.cell.setAttribute(
        "aria-sort",
        active ? (self.dir === "asc" ? "ascending" : "descending") : "none"
      );
      header.mark.textContent = active ? (self.dir === "asc" ? "▲" : "▼") : "";
    });
    this.queryChanged(false);
  };

  TableView.prototype.clearFilters = function () {
    var self = this;
    this.q = "";
    this.search.value = "";
    this.filters = {};
    Object.keys(this.filterInputs).forEach(function (key) { self.filterInputs[key].value = ""; });
    if (this.onlyBox) { this.onlyBox.checked = false; }
    this.only = false;
    this.queryChanged(false);
  };

  /* Starts a new query: older answers are ignored from now on. */
  TableView.prototype.reload = function () {
    this.gen += 1;
    this.dirty = false;
    this.pages.clear();
    this.inflight.clear();
    this.failed = false;
    this.total = null;
    this.scroller.scrollTop = 0;
    this.count.textContent = "Loading…";
    this.fetchPage(0);
    this.render();
  };

  /* The model changed: fetch again now if this table is on screen, else when it is shown. */
  TableView.prototype.invalidate = function () {
    this.dirty = true;
    if (!this.panel.hidden) { this.reload(); }
  };

  TableView.prototype.activate = function () {
    if (this.dirty) { this.reload(); } else { this.render(); }
  };

  TableView.prototype.fetchPage = function (page) {
    if (this.pages.has(page) || this.inflight.has(page) || this.failed) { return; }
    var self = this;
    var gen = this.gen;
    this.inflight.add(page);
    PT.get("/api/tables/" + encodeURIComponent(this.def.name) + "?" + this.queryString(page * PAGE)).then(
      function (answer) {
        if (gen !== self.gen) { return; } // an answer to an older query: ignore it
        self.inflight.delete(page);
        if (answer.status === "preparing") {
          self.count.textContent = "Preparing the table…";
          window.setTimeout(function () {
            if (gen === self.gen) { self.fetchPage(page); }
          }, 300);
          return;
        }
        self.total = answer.total;
        self.filtered = answer.filtered;
        self.pages.set(page, answer.rows);
        self.note = answer.status === "no-model" ? "The repo is not loaded." : (answer.note || "");
        self.trimPages(page);
        self.updateCount();
        self.render();
      },
      function (error) {
        if (gen !== self.gen) { return; }
        self.inflight.delete(page);
        self.failed = true;
        self.count.textContent = "Could not load the rows";
        PT.showError(error, "Table " + self.def.title);
        self.render();
      }
    );
  };

  TableView.prototype.trimPages = function (keep) {
    if (this.pages.size <= MAX_PAGES) { return; }
    var self = this;
    var far = Array.from(this.pages.keys()).sort(function (a, b) {
      return Math.abs(b - keep) - Math.abs(a - keep);
    });
    far.slice(0, this.pages.size - MAX_PAGES).forEach(function (page) { self.pages.delete(page); });
  };

  TableView.prototype.updateCount = function () {
    if (this.total === null) { return; }
    this.count.textContent = PT.fmt(this.filtered) + " of " + PT.fmt(this.total) + " rows";
    this.scroller.setAttribute("aria-rowcount", String(this.filtered));
  };

  /* ---- drawing */

  TableView.prototype.scheduleRender = function () {
    var self = this;
    if (this.frame !== null) { return; }
    this.frame = window.requestAnimationFrame(function () {
      self.frame = null;
      self.render();
    });
  };

  TableView.prototype.render = function () {
    if (this.panel.hidden) { return; }
    var self = this;
    var count = this.total === null ? 0 : this.filtered;
    this.body.style.height = count * ROW_H + "px";

    var loaded = this.total !== null;
    this.empty.hidden = !(loaded && count === 0) && !this.failed;
    if (this.failed) {
      this.empty.textContent = "The rows could not be loaded. Change a filter or reload to try again.";
    } else if (loaded && count === 0) {
      this.empty.textContent = this.note || (this.total === 0 ? "This table is empty." : "No row matches the filters.");
    }

    var visible = Math.max(0, this.scroller.clientHeight - this.head.offsetHeight);
    var first = Math.max(0, Math.floor(this.scroller.scrollTop / ROW_H) - OVERSCAN);
    var last = Math.min(count, Math.ceil((this.scroller.scrollTop + visible) / ROW_H) + OVERSCAN);

    var fragment = document.createDocumentFragment();
    var missing = [];
    for (var index = first; index < last; index += 1) {
      var page = Math.floor(index / PAGE);
      var rows = this.pages.get(page);
      var row = rows ? rows[index - page * PAGE] : undefined;
      if (!rows && missing.indexOf(page) < 0) { missing.push(page); }
      fragment.appendChild(this.rowElement(index, row));
    }
    PT.clear(this.windowEl);
    this.windowEl.style.transform = "translateY(" + first * ROW_H + "px)";
    this.windowEl.appendChild(fragment);

    if (missing.length) {
      if (this.fetchTimer) { window.clearTimeout(this.fetchTimer); }
      this.fetchTimer = window.setTimeout(function () {
        self.fetchTimer = null;
        missing.forEach(function (page) { self.fetchPage(page); });
      }, FETCH_DELAY);
    }
  };

  TableView.prototype.rowElement = function (index, row) {
    var element = PT.make("div", "vt-row" + (index % 2 ? " odd" : "") + (row ? "" : " loading"));
    element.setAttribute("role", "row");
    element.setAttribute("aria-rowindex", String(index + 1));
    element.dataset.index = String(index);
    var columns = this.def.columns;
    for (var c = 0; c < columns.length; c += 1) {
      var text = row ? row[c] : (c === 0 ? "…" : "");
      var cell = PT.make("div", columns[c].numeric ? "vt-cell numeric" : "vt-cell", text);
      cell.setAttribute("role", "gridcell");
      cell.dir = "auto";
      if (text && text.length > 12) { cell.title = text; }
      element.appendChild(cell);
    }
    return element;
  };

  /* Arrow keys and PageUp / PageDown scroll when the table (not an input) has the focus. */
  TableView.prototype.onKey = function (event) {
    if (event.target !== this.scroller || event.ctrlKey || event.altKey || event.metaKey) { return; }
    var page = Math.max(ROW_H, this.scroller.clientHeight - this.head.offsetHeight - ROW_H);
    var step = {
      ArrowDown: ROW_H, ArrowUp: -ROW_H, PageDown: page, PageUp: -page,
      Home: -this.scroller.scrollHeight, End: this.scroller.scrollHeight
    }[event.key];
    if (step === undefined) { return; }
    event.preventDefault();
    this.scroller.scrollTop += step;
  };

  TableView.prototype.focusSearch = function () {
    this.search.focus();
    this.search.select();
  };

  PT.TableView = TableView;
})();
