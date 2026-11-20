/* LEMIT 공통 동작 (#45). 프레임워크 없이 점진적 향상: JS가 없어도 모든 화면이 동작한다. */
(function () {
  "use strict";
  var root = document.documentElement;

  /* 1. 모바일·태블릿 접이식 메뉴 */
  var toggle = document.querySelector("[data-nav-toggle]");
  var sidebar = document.getElementById("sidebar");
  var backdrop = document.querySelector(".nav-backdrop");
  var mq = window.matchMedia("(max-width: 1023px)");

  function setOpen(open, restoreFocus) {
    if (!toggle || !sidebar) return;
    root.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "메뉴 닫기" : "메뉴 열기");
    if (backdrop) backdrop.hidden = !open;
    if (open) {
      /* 스타일을 먼저 반영해 숨김(visibility: hidden)을 푼 뒤 포커스를 옮긴다 */
      void sidebar.offsetWidth;
      var first = sidebar.querySelector("a[href], button:not([disabled])");
      if (first) first.focus();
    } else if (restoreFocus) {
      toggle.focus();
    }
  }

  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      setOpen(!root.classList.contains("nav-open"), false);
    });
    document.querySelectorAll("[data-nav-close]").forEach(function (el) {
      el.addEventListener("click", function () { setOpen(false, true); });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && root.classList.contains("nav-open")) setOpen(false, true);
    });
    /* 메뉴 안에서 Tab 이동이 바깥으로 새지 않게 (모달처럼 동작) */
    sidebar.addEventListener("keydown", function (e) {
      if (e.key !== "Tab" || !mq.matches || !root.classList.contains("nav-open")) return;
      var items = sidebar.querySelectorAll("a[href], button:not([disabled])");
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    /* 화면이 넓어지면 열린 상태를 정리 */
    mq.addEventListener("change", function (e) { if (!e.matches) setOpen(false, false); });
  }

  /* 2. 값이 바뀌면 폼 제출 (수강생 '보관 포함' 체크) */
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { if (el.form) el.form.submit(); });
  });

  /* 3. 제출 전 확인 (보관 등) */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
})();
