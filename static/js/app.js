/* LEMIT 공통 동작 (#45). 프레임워크 없이 점진적 향상: JS가 없어도 모든 화면이 동작한다. */
(function () {
  "use strict";
  var root = document.documentElement;

  /* 1. 모바일·태블릿 접이식 메뉴 */
  var toggle = document.querySelector("[data-nav-toggle]");
  var sidebar = document.getElementById("sidebar");
  var backdrop = document.querySelector(".nav-backdrop");
  var mq = window.matchMedia("(max-width: 1023px)");

  var mainCol = document.querySelector(".main-col");
  var skipLink = document.querySelector(".skip-link");

  function setOpen(open, restoreFocus) {
    if (!toggle || !sidebar) return;
    root.classList.toggle("nav-open", open);
    /* 열린 동안 배경(헤더·본문)은 키보드·화면낭독기에서 제외하고, 메뉴를 모달 대화상자로 알린다 */
    if (mainCol) mainCol.inert = open;
    if (skipLink) skipLink.inert = open;
    if (open) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
    }
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
    /* 동작 연결이 끝난 뒤에만 접이식 메뉴 스타일을 켠다. 이 파일을 못 불러오면 메뉴가 위쪽에 펼쳐진 채로 남는다 */
    root.classList.replace("no-js", "js");
  }

  /* 2. 값이 바뀌면 폼 제출 (수강생 '보관 포함' 체크) */
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { if (el.form) el.form.submit(); });
  });

  /* 3. 저장 버튼은 누르면 바로 비활성화 (중복 제출 방지, S07). 서버는 저장 후 항상 리다이렉트한다 */
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      form.querySelectorAll("button[type=submit]").forEach(function (b) {
        setTimeout(function () { b.disabled = true; }, 0);  // 제출 이후에 막아야 버튼 값이 전송에서 빠지지 않는다
      });
    });
  });

  /* 4-1. 레슨명: '직접 입력'을 고를 때만 새 레슨명 칸을 보인다 (#75, S11). JS가 없으면 칸이 항상 보인다 */
  document.querySelectorAll("[data-custom-toggle]").forEach(function (box) {
    var radios = box.closest("form").querySelectorAll('input[name="' + box.getAttribute("data-custom-toggle") + '"]');
    function sync(focus) {
      var picked = Array.prototype.find.call(radios, function (r) { return r.checked; });
      box.hidden = !!picked && picked.value !== "__custom__";
      var input = box.querySelector("input");
      if (focus && !box.hidden && input) input.focus();
    }
    radios.forEach(function (r) { r.addEventListener("change", function () { sync(true); }); });
    sync(false);
  });

  /* 4-2. 체크박스를 켰을 때만 딸린 입력 칸을 보인다 (#76 회차 자동 생성). JS가 없으면 항상 보인다 */
  document.querySelectorAll("[data-show-if-checked]").forEach(function (box) {
    var check = box.closest("form").querySelector('input[name="' + box.getAttribute("data-show-if-checked") + '"]');
    if (!check) return;
    function sync() {
      box.hidden = !check.checked;
      /* 숨긴 칸은 보내지 않는다: 이전에 입력한 잘못된 값이 숨은 채 저장을 막지 않게 (PR #87 리뷰 P2) */
      box.querySelectorAll("input, select, textarea").forEach(function (el) { el.disabled = box.hidden; });
    }
    check.addEventListener("change", sync);
    sync();
  });

  /* 4-3. 수강생 대화방 (#95): 맨 아래로 이동, PC는 Enter 전송, 화면을 보는 동안 새 메시지 확인.
     확인 간격은 10초 → (새 메시지가 없으면) 30초 → 60초, 새 메시지가 오면 다시 10초.
     화면이 가려지면 멈췄다가 다시 보이면 바로 확인, 10분 동안 입력이 없으면 멈추고 새로고침 안내 */
  var chat = document.querySelector("[data-chat]");
  if (chat) {
    var list = chat.querySelector("[data-chat-list]");
    var input = chat.querySelector("[data-chat-input]");
    var paused = chat.querySelector("[data-chat-paused]");
    var empty = chat.querySelector("[data-chat-empty]");
    var pollUrl = chat.getAttribute("data-poll-url");
    var lastId = list.getAttribute("data-last-id") || "0";
    var delays = [10000, 30000, 60000];
    var step = 0, timer = null, stopped = false, inFlight = false, lastActive = Date.now();
    var IDLE_LIMIT = 10 * 60 * 1000;
    var toEnd = function () { var end = document.getElementById("chat-end"); if (end) end.scrollIntoView({ block: "end" }); };
    toEnd();
    if (input) {
      var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
      input.addEventListener("keydown", function (e) {
        /* 한글 입력 중(조합 중)의 Enter는 글자 확정이라 보내지 않는다. 휴대폰은 Enter가 줄바꿈, 버튼으로 전송 */
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229 && !coarse) {
          e.preventDefault();
          if (input.value.trim() && input.form) input.form.requestSubmit ? input.form.requestSubmit() : input.form.submit();
        }
      });
    }
    var schedule = function () {
      clearTimeout(timer);
      if (stopped || document.hidden) return;
      timer = setTimeout(check, delays[Math.min(step, delays.length - 1)]);
    };
    var stop = function () { stopped = true; clearTimeout(timer); if (paused) paused.hidden = false; };
    /* 이미 화면에 있는 말풍선(data-id)은 다시 붙이지 않는다. 요청이 겹치거나 응답이 늦게 와도 한 번만 보이게 */
    var appendNew = function (html) {
      var box = document.createElement("template");
      box.innerHTML = html;
      Array.prototype.forEach.call(box.content.querySelectorAll(".chat-msg[data-id]"), function (li) {
        if (list.querySelector('.chat-msg[data-id="' + li.getAttribute("data-id") + '"]')) {
          var prev = li.previousElementSibling;
          if (prev && prev.classList.contains("chat-day")) prev.remove();
          li.remove();
        }
      });
      list.appendChild(box.content);
    };
    var check = function () {
      if (inFlight) return;  /* 요청은 한 번에 하나만 (#96 리뷰: 탭 전환 중 같은 커서로 두 번 요청하던 문제) */
      if (Date.now() - lastActive > IDLE_LIMIT) { stop(); return; }
      inFlight = true;
      clearTimeout(timer);
      fetch(pollUrl + "?after=" + encodeURIComponent(lastId), { credentials: "same-origin", redirect: "manual", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error("poll " + r.status); return r.json(); })
        .then(function (d) {
          inFlight = false;
          if (d.count) {
            appendNew(d.html);
            if (Number(d.last_id) > Number(lastId)) lastId = String(d.last_id);
            step = 0;
            if (empty) empty.hidden = true;
            toEnd();
          } else {
            step += 1;
          }
          schedule();
        })
        .catch(function () { inFlight = false; stop(); });  /* 로그아웃·오류면 조용히 멈추고 새로고침 안내 */
    };
    ["keydown", "pointerdown", "touchstart"].forEach(function (ev) {
      document.addEventListener(ev, function () { lastActive = Date.now(); }, { passive: true });
    });
    document.addEventListener("visibilitychange", function () {
      if (stopped) return;
      if (document.hidden) { clearTimeout(timer); } else { step = 0; check(); }
    });
    schedule();
  }

  /* 4. 제출 전 확인 (보관 등) */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  /* 5. 복사 버튼 (API 키 발급 직후, #102). 클립보드를 못 쓰면 입력란을 선택해 직접 복사하게 한다 */
  document.querySelectorAll("[data-copy-target]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.getAttribute("data-copy-target"));
      if (!input) return;
      input.focus();
      input.select();
      var done = function () { btn.textContent = btn.getAttribute("data-copied-text") || "복사했어요"; };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(input.value).then(done, function () {});
      } else if (document.execCommand && document.execCommand("copy")) {
        done();
      }
    });
  });
  document.querySelectorAll("[data-select-on-focus]").forEach(function (input) {
    input.addEventListener("focus", function () { input.select(); });
  });
})();
