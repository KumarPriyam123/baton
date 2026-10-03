/**
 * Keyboard shortcuts (DESIGN §6.3). Ignored while typing in a field, while a dialog is open, and
 * when a modifier is held. `j`/`k` move focus between strips, so Enter follows the focused link.
 */
import { useEffect } from "react";

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function moveStripFocus(direction: 1 | -1): void {
  const strips = [...document.querySelectorAll<HTMLElement>("[data-strip]")];
  if (strips.length === 0) return;
  const current = strips.indexOf(document.activeElement as HTMLElement);
  const next = current === -1 ? (direction === 1 ? 0 : strips.length - 1) : current + direction;
  strips[Math.min(Math.max(next, 0), strips.length - 1)]?.focus();
}

export interface ShortcutHandlers {
  newRequest: () => void;
  focusSearch: () => void;
  goTo: (place: "inbox" | "queue") => void;
  closeDetail: () => void;
}

export function useShortcuts(handlers: ShortcutHandlers): void {
  useEffect(() => {
    let pendingG = false;
    let timer: number | undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      if (document.querySelector('[role="dialog"]')) return;

      if (pendingG) {
        pendingG = false;
        window.clearTimeout(timer);
        if (event.key === "i") handlers.goTo("inbox");
        else if (event.key === "q") handlers.goTo("queue");
        else return;
        event.preventDefault();
        return;
      }

      switch (event.key) {
        case "j":
          moveStripFocus(1);
          break;
        case "k":
          moveStripFocus(-1);
          break;
        case "/":
          handlers.focusSearch();
          break;
        case "c":
          handlers.newRequest();
          break;
        case "Escape":
          handlers.closeDetail();
          return;
        case "g":
          pendingG = true;
          timer = window.setTimeout(() => {
            pendingG = false;
          }, 1000);
          return;
        default:
          return;
      }
      event.preventDefault();
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.clearTimeout(timer);
    };
  }, [handlers]);
}
