"use strict";
// Shared by work conversations and Brain. "Latest" means a readable message,
// not the end of the page (which also includes controls and the composer).
window.PilotReading = {
  latest(list) {
    const messages = Array.from(list.children);
    return messages.findLast(message => message.dataset.readingTarget !== "false") || messages.at(-1);
  },
  start(list, scroll, inset = 12) {
    const message = this.latest(list);
    if (!message) { scroll.scrollTop = scroll.scrollHeight; return; }
    const viewportTop = scroll === document.scrollingElement ? 0 : scroll.getBoundingClientRect().top + scroll.clientTop;
    scroll.scrollTop = Math.max(0, scroll.scrollTop + message.getBoundingClientRect().top - viewportTop - inset);
  }
};
