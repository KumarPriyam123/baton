/**
 * Screen-reader announcements (DESIGN §7). Live changes are polite; conflicts are assertive.
 * The two regions live in the shell, so they exist before anything is said (a region added at the
 * same moment as its text is often not announced).
 */
export function announce(message: string, assertive = false): void {
  const region = document.getElementById(assertive ? "live-region-assertive" : "live-region");
  if (!region) return;
  // Clear first so the same sentence twice in a row is still read out.
  region.textContent = "";
  window.setTimeout(() => {
    region.textContent = message;
  }, 50);
}
