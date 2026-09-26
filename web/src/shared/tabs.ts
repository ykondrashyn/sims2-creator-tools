/** Manual activation follows the WAI-ARIA tabs pattern. */
export function installTabs(
  root: HTMLElement,
  onActivate: (tab: HTMLButtonElement) => void = () => {},
) {
  const tabs = Array.from(
    root.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
  );
  const available = () => tabs.filter((tab) => !tab.hidden && !tab.disabled);
  const focus = (tab: HTMLButtonElement) => {
    tab.focus();
  };
  for (const [index, tab] of tabs.entries()) {
    tab.id ||= `creator-tab-${index}`;
    const panel = document.getElementById(
      tab.getAttribute("aria-controls") || "",
    );
    if (!panel)
      throw new Error(
        "A creator tab is missing its panel. Reload the website.",
      );
    panel.setAttribute("aria-labelledby", tab.id);
    panel.tabIndex = 0;
    tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
    tab.addEventListener("click", () => {
      for (const item of tabs) {
        const selected = item === tab;
        item.tabIndex = selected ? 0 : -1;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
        const controlled = document.getElementById(
          item.getAttribute("aria-controls") || "",
        );
        if (controlled) {
          controlled.hidden = !selected;
          controlled.dispatchEvent(
            new CustomEvent("creatorvisibilitychange", {
              detail: { visible: selected },
            }),
          );
        }
      }
      onActivate(tab);
    });
    tab.addEventListener("keydown", (event) => {
      const shown = available(),
        at = shown.indexOf(tab);
      let next: HTMLButtonElement | undefined;
      if (event.key === "ArrowRight") next = shown[(at + 1) % shown.length];
      if (event.key === "ArrowLeft")
        next = shown[(at + shown.length - 1) % shown.length];
      if (event.key === "Home") next = shown[0];
      if (event.key === "End") next = shown[shown.length - 1];
      if (next) {
        event.preventDefault();
        focus(next);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        tab.click();
      }
    });
  }
}
