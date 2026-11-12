"use strict";
/* PortTool page, part 1: the session token, calls to the local server, messages.
   Every script adds to the one global object PT. Data is always written with
   textContent, never as HTML. */

var PT = {};

(function () {
  var token = "";
  var MAX_MESSAGES = 6;

  /* The address opened by 'porttool gui' carries the token once. It is kept in memory
     only and removed from the address bar. Returns false when there is no token. */
  PT.initToken = function () {
    var params = new URLSearchParams(window.location.search);
    token = params.get("token") || "";
    if (token && window.history && window.history.replaceState) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    return token !== "";
  };

  function ApiFailure(code, message, status) {
    this.code = code;
    this.message = message;
    this.status = status;
  }
  PT.ApiFailure = ApiFailure;

  /* Calls the server. Resolves with the parsed JSON, rejects with an ApiFailure. */
  PT.request = function (method, path, options) {
    var init = { method: method, headers: { "X-PortTool-Token": token }, cache: "no-store" };
    if (options && options.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.json);
    } else if (options && options.body !== undefined) {
      init.body = options.body;
    }
    return fetch(path, init).then(
      function (response) {
        return response.text().then(function (text) {
          var data = null;
          try {
            data = text ? JSON.parse(text) : null;
          } catch (parseError) {
            data = null;
          }
          if (!response.ok) {
            var error = data && data.error ? data.error : null;
            if (error && error.code === "stopped") {
              // An open connection answered after 'porttool gui' was stopped.
              PT.connectionLost(true);
              throw new ApiFailure("no-connection", error.message, response.status);
            }
            throw new ApiFailure(
              error ? error.code : "http-" + response.status,
              error ? error.message : "The server answered with status " + response.status + ".",
              response.status
            );
          }
          if (data === null) {
            throw new ApiFailure("bad-answer", "The server sent an answer that is not JSON.", response.status);
          }
          PT.connectionLost(false);
          return data;
        });
      },
      function () {
        PT.connectionLost(true);
        throw new ApiFailure(
          "no-connection",
          "PortTool does not answer. Is the 'porttool gui' window still open?",
          0
        );
      }
    );
  };
  PT.get = function (path) { return PT.request("GET", path); };
  PT.post = function (path, json) { return PT.request("POST", path, { json: json || {} }); };
  PT.upload = function (path, formData) { return PT.request("POST", path, { body: formData }); };

  /* ---- small helpers */

  PT.el = function (id) {
    var element = document.getElementById(id);
    if (!element) { throw new Error("The page has no element '" + id + "'."); }
    return element;
  };

  /* Creates an element. Text is set with textContent, so markup in data stays text. */
  PT.make = function (tag, className, text) {
    var element = document.createElement(tag);
    if (className) { element.className = className; }
    if (text !== undefined && text !== null) { element.textContent = String(text); }
    return element;
  };

  PT.clear = function (element) {
    while (element.firstChild) { element.removeChild(element.firstChild); }
  };

  PT.fmt = function (number) {
    return Number(number).toLocaleString("en-US");
  };

  PT.duration = function (seconds) {
    var total = Math.max(0, Math.round(Number(seconds) || 0));
    var minutes = Math.floor(total / 60);
    var rest = total % 60;
    return minutes > 0 ? minutes + " min " + (rest < 10 ? "0" : "") + rest + " s" : rest + " s";
  };

  PT.errorText = function (error) {
    if (error instanceof ApiFailure) { return "[" + error.code + "] " + error.message; }
    if (error && error.message) { return String(error.message); }
    return String(error);
  };

  /* ---- messages under the header */

  function addMessage(kind, text) {
    var box = PT.el("messages");
    var last = box.lastElementChild;
    if (last && last.dataset.text === text) {
      var count = Number(last.dataset.count || "1") + 1;
      last.dataset.count = String(count);
      last.querySelector(".msg-text").textContent = text + "  (" + count + " times)";
      return last;
    }
    var message = PT.make("div", "msg msg-" + kind);
    message.setAttribute("role", kind === "error" ? "alert" : "status");
    message.dataset.text = text;
    message.appendChild(PT.make("span", "msg-text", text));
    var close = PT.make("button", "btn btn-small msg-close", "Dismiss");
    close.type = "button";
    close.addEventListener("click", function () { message.remove(); });
    message.appendChild(close);
    box.appendChild(message);
    while (box.children.length > MAX_MESSAGES) { box.removeChild(box.firstElementChild); }
    return message;
  }

  /* Shows a failed call or any other error. 'what' says what was being done. */
  PT.showError = function (error, what) {
    if (error instanceof ApiFailure && error.code === "no-connection") { return; } // own banner
    addMessage("error", (what ? what + ": " : "") + PT.errorText(error));
  };
  PT.showInfo = function (text) { addMessage("info", text); };

  var lostMessage = null;
  PT.connectionLost = function (lost) {
    if (lost && !lostMessage) {
      lostMessage = addMessage(
        "error",
        "[no-connection] PortTool does not answer. Is the 'porttool gui' window still open? " +
          "This page tries again every few seconds."
      );
      lostMessage.querySelector(".msg-close").hidden = true;
    } else if (!lost && lostMessage) {
      lostMessage.remove();
      lostMessage = null;
    }
  };

  /* Last resort: print the error on the page so the screen is never blank. */
  PT.fatal = function (error) {
    var box = document.getElementById("fatal");
    var text = "PortTool page error: " + (error && error.stack ? error.stack : PT.errorText(error));
    if (!box) {
      document.body.appendChild(document.createTextNode(text));
      return;
    }
    box.hidden = false;
    box.textContent = (box.textContent ? box.textContent + "\n\n" : "") + text;
  };
})();
