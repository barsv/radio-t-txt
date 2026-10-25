"use strict";
(() => {
  const audio = document.getElementById("podcast");
  if (!audio) return;
  const blocks = [...document.querySelectorAll(".transcript-block")];
  const chapters = [...document.querySelectorAll(".chapter")];
  const topic = document.getElementById("current-topic");
  const message = document.getElementById("player-message");
  const follow = document.getElementById("follow");
  let active = null;
  let pendingSeek = null;
  let pendingPlay = false;

  function applySeek() {
    if (pendingSeek === null) return;
    audio.currentTime = pendingSeek;
    pendingSeek = null;
    if (pendingPlay) audio.play().catch(error => {
      if (error.name === "NotAllowedError") message.textContent = "Нажмите ▶ в плеере";
      else if (error.name !== "AbortError") message.textContent = "Не удалось запустить запись";
    });
    pendingPlay = false;
  }
  function seek(seconds, play) {
    pendingSeek = seconds;
    pendingPlay = play;
    if (audio.readyState >= 1) applySeek();
    else { audio.preload = "metadata"; audio.load(); }
  }
  audio.addEventListener("loadedmetadata", applySeek);
  document.addEventListener("click", event => {
    const control = event.target.closest("[data-seek]");
    if (!control) return;
    const seconds = Number(control.dataset.seek);
    if (!Number.isFinite(seconds)) return;
    seek(seconds, true);
    const block = control.closest(".transcript-block");
    if (block) block.closest("details").open = true;
  });
  document.getElementById("speed").addEventListener("change", event => {
    audio.playbackRate = Number(event.target.value);
  });
  audio.addEventListener("error", () => {
    message.textContent = "Не удалось загрузить оригинальное аудио";
  });
  audio.addEventListener("playing", () => { message.textContent = ""; });
  audio.addEventListener("timeupdate", () => {
    const current = audio.currentTime;
    let low = 0, high = blocks.length - 1, index = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (Number(blocks[mid].dataset.start) <= current) { index = mid; low = mid + 1; }
      else high = mid - 1;
    }
    const next = index >= 0 ? blocks[index] : null;
    if (next === active) return;
    if (active) active.classList.remove("playing");
    active = next;
    if (active) {
      active.classList.add("playing");
      const section = active.closest(".chapter");
      topic.textContent = section.querySelector("h2").textContent;
      if (follow.checked) {
        active.closest("details").open = true;
        active.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  });
  function revealHash() {
    const id = location.hash.slice(1);
    const element = document.getElementById(id);
    if (!element) return;
    const details = element.closest("details");
    if (details) details.open = true;
    if (element.dataset.start) seek(Number(element.dataset.start), false);
  }
  window.addEventListener("hashchange", () => {
    const element = document.getElementById(location.hash.slice(1));
    if (element?.closest("details")) element.closest("details").open = true;
  });
  if (location.hash) revealHash();
  if ("IntersectionObserver" in window) {
    const nav = [...document.querySelectorAll(".contents nav a")];
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        for (const link of nav) link.classList.toggle("active", link.hash === "#" + entry.target.id);
      }
    }, { rootMargin: "-10% 0px -70% 0px" });
    chapters.forEach(section => observer.observe(section));
  }
})();
