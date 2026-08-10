/* Reading the email back after a person types in it.

   The email is the page: the subject, the section headings and the lists are
   contenteditable, and leaving a field saves it. Deleting a line is how a line
   is cut, so a section is saved whole — the server compares what came back with
   what it sent and treats the missing lines as cut from this audience.

   Links inside the email do not follow while it is being written; they are for
   the person who receives it. */

(function () {
  var mail = document.getElementById("mail");
  if (!mail) return;

  var url = mail.dataset.save;
  var audience = mail.dataset.t;
  var csrf = mail.dataset.csrf;

  function post(body) {
    body.set("t", audience);
    body.set("csrfmiddlewaretoken", csrf);
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" });
  }

  function saveSubject(el) {
    var body = new FormData();
    body.set("what", "subject");
    body.set("value", el.textContent.trim());
    post(body);
  }

  function saveTitle(el) {
    var body = new FormData();
    body.set("what", "section-title");
    body.set("sec", el.dataset.secTitle);
    body.set("value", el.textContent.trim());
    post(body);
  }

  // A line the person wrote has no id; the server gives it one. A line whose
  // text is gone is not sent back at all, which is what cuts it.
  // The line reads as one sentence, so the title is everything on it that is
  // not one of the parts with a home of its own: the day and time, the note
  // underneath, the arrow, the flags. Reading only the link's own text lost
  // whatever was typed after it (golda 2026-08-10).
  var NOT_TITLE = ".mail-when, .mail-note, .mail-go, .mail-opt";

  function titleOf(li) {
    var copy = li.cloneNode(true);
    [].forEach.call(copy.querySelectorAll(NOT_TITLE), function (part) { part.remove(); });
    return copy.textContent.replace(/\s+/g, " ").trim();
  }

  function saveSection(el) {
    var rows = [].map.call(el.querySelectorAll("li"), function (li) {
      var note = li.querySelector(".mail-note");
      return {
        id: li.dataset.id || "",
        title: titleOf(li),
        note: note ? note.textContent.trim() : "",
      };
    });
    var body = new FormData();
    body.set("what", el.dataset.sec);
    body.set("rows", JSON.stringify(rows));
    post(body)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // Only when the set of lines changed: a new line has to pick up its id
        // and a cut one has to appear as a chip. Editing words redraws nothing.
        if (d && d.redraw) window.location.reload();
      });
  }

  document.querySelectorAll("[contenteditable]").forEach(function (el) {
    el.addEventListener("blur", function () {
      if (el.dataset.f === "subject") return saveSubject(el);
      if (el.dataset.secTitle) return saveTitle(el);
      if (el.dataset.sec) return saveSection(el);
    });
  });

  // A click inside the sheet is a click into the text: following a link would
  // take the caret away mid-edit. The arrow beside a line is the way to go
  // there, so it is the one anchor that keeps its click.
  mail.addEventListener("click", function (e) {
    var a = e.target.closest("a");
    if (a && !a.classList.contains("mail-go")) e.preventDefault();
  });
})();
