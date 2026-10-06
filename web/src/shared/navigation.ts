import {
  destinations,
  groups,
  resolveDestination,
  type Destination,
  type DestinationId,
} from "./destinations.js";

export type Activation = () => Promise<void>;
export type Activators = Partial<Record<DestinationId, Activation>>;

/** Coalesce initialization and retain successes. Failed activations can be retried. */
export function createActivationLoader(activators: Activators) {
  const pending = new Map<DestinationId, Promise<void>>();
  return (id: DestinationId) => {
    const existing = pending.get(id);
    if (existing) return existing;
    const activation = Promise.resolve()
      .then(() => activators[id]?.())
      .catch((error: unknown) => {
        pending.delete(id);
        throw error;
      });
    pending.set(id, activation);
    return activation;
  };
}

export function setPanelVisibility(panel: HTMLElement, visible: boolean) {
  panel.hidden = !visible;
  panel.dispatchEvent(
    new CustomEvent("creatorvisibilitychange", { detail: { visible } }),
  );
}

/** Loading cannot be aborted safely once a controller has installed handlers. */
export function createActivationSequence(activators: Activators) {
  const activate = createActivationLoader(activators);
  let revision = 0;
  return async (id: DestinationId) => {
    const request = ++revision;
    try {
      await activate(id);
      return request === revision ? { status: "ready" as const } : null;
    } catch (error) {
      return request === revision ? { status: "error" as const, error } : null;
    }
  };
}

function node<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
  className?: string,
) {
  const element = document.createElement(tag);
  if (text) element.textContent = text;
  if (className) element.className = className;
  return element;
}

export function installNavigation(activators: Activators) {
  const get = <T extends HTMLElement>(id: string) =>
    document.getElementById(id) as T;
  const main = get<HTMLElement>("workspace");
  const navigation = get<HTMLElement>("tool-navigation");
  const cards = get<HTMLElement>("home-cards");
  const drawer = get<HTMLDialogElement>("tool-drawer");
  const drawerSlot = get<HTMLElement>("drawer-navigation");
  const sidebar = get<HTMLElement>("sidebar-navigation");
  const opener = get<HTMLButtonElement>("tools-toggle");
  const currentLabel = get<HTMLElement>("current-destination");
  const feedback = get<HTMLElement>("navigation-feedback");
  const feedbackHeading = get<HTMLElement>("navigation-heading");
  const message = get<HTMLElement>("navigation-message");
  const retry = get<HTMLButtonElement>("navigation-retry");
  const notice = get<HTMLElement>("navigation-notice");
  const wide = matchMedia("(min-width: 1200px)");
  const activate = createActivationSequence(activators);
  let current = destinations[0];
  for (const destination of destinations) {
    const panel = get(destination.panel);
    const heading = panel.querySelector<HTMLElement>("h1,h2");
    if (heading) {
      heading.id ||= `heading-${destination.id}`;
      panel.setAttribute("aria-labelledby", heading.id);
    }
  }

  function link(destination: Destination, card = false) {
    const anchor = node("a", undefined, card ? "tool-card" : "tool-link");
    anchor.href = `#/${destination.route}`;
    anchor.dataset.destination = destination.id;
    if (!card) anchor.dataset.tab = destination.id;
    const label = node(card ? "h3" : "span", destination.label, "tool-label");
    anchor.append(label);
    if (destination.experimental)
      anchor.append(node("span", "Experimental", "experimental-badge"));
    if (destination.subtitle)
      anchor.append(node("span", destination.subtitle, "tool-subtitle"));
    if (card)
      anchor.append(
        node("p", destination.description),
        node("span", destination.output, "tool-output"),
      );
    return anchor;
  }
  navigation.append(link(destinations[0]));
  groups.forEach((group, index) => {
    const section = node("section", undefined, "navigation-group");
    const heading = node("h2", group);
    heading.id = `navigation-group-${index}`;
    const list = node("ul");
    section.setAttribute("aria-labelledby", heading.id);
    const cardSection = node("section", undefined, "home-group");
    const cardHeading = node("h2", group);
    const grid = node("div", undefined, "tool-card-grid");
    for (const destination of destinations.filter(
      (item) => item.group === group,
    )) {
      const item = node("li");
      item.append(link(destination));
      list.append(item);
      grid.append(link(destination, true));
    }
    section.append(heading, list);
    navigation.append(section);
    cardSection.append(cardHeading, grid);
    cards.append(cardSection);
  });

  function closeDrawer() {
    if (drawer.open) drawer.close();
    opener.setAttribute("aria-expanded", "false");
  }
  function resize() {
    const wasOpen = drawer.open;
    // A user can open the drawer after CSS changes but before matchMedia fires.
    // Keep that action intact when moving into the narrow layout.
    if (wide.matches) closeDrawer();
    (wide.matches ? sidebar : drawerSlot).append(navigation);
    if (wasOpen)
      navigation.querySelector<HTMLElement>('[aria-current="page"]')?.focus();
  }
  resize();
  wide.addEventListener("change", resize);
  opener.addEventListener("click", () => {
    drawer.showModal();
    opener.setAttribute("aria-expanded", "true");
    (
      navigation.querySelector<HTMLElement>('[aria-current="page"]') ||
      get("tools-close")
    ).focus();
  });
  get("tools-close").addEventListener("click", closeDrawer);
  drawer.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = Array.from(
      drawer.querySelectorAll<HTMLElement>("button:not([disabled]),a[href]"),
    );
    const first = controls[0],
      last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  drawer.addEventListener("close", () =>
    opener.setAttribute("aria-expanded", "false"),
  );
  drawer.addEventListener("click", (event) => {
    if (event.target !== drawer) return;
    const rect = drawer.getBoundingClientRect();
    if (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    )
      closeDrawer();
  });

  function focusHeading(heading: HTMLElement) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: true });
  }
  async function show(destination: Destination, focus: boolean) {
    current = destination;
    closeDrawer();
    currentLabel.textContent = destination.label;
    document.title =
      destination.id === "home"
        ? "Sims 2 Creator Tools"
        : `${destination.label} · Sims 2 Creator Tools`;
    for (const anchor of navigation.querySelectorAll<HTMLAnchorElement>("a")) {
      if (anchor.dataset.destination === destination.id)
        anchor.setAttribute("aria-current", "page");
      else anchor.removeAttribute("aria-current");
    }
    for (const item of destinations) setPanelVisibility(get(item.panel), false);
    feedbackHeading.textContent = destination.label;
    message.textContent = `Loading ${destination.label}…`;
    retry.hidden = true;
    feedback.hidden = destination.id === "home";
    main.setAttribute("aria-busy", "true");
    const result = await activate(destination.id);
    if (!result) return;
    main.removeAttribute("aria-busy");
    if (result.status === "ready") {
      feedback.hidden = true;
      const panel = get(destination.panel);
      setPanelVisibility(panel, true);
      if (focus) {
        focusHeading(panel.querySelector<HTMLElement>("h1,h2") || panel);
        main.scrollIntoView({ block: "start" });
      }
    } else {
      const error = result.error;
      feedback.hidden = false;
      message.textContent =
        error instanceof Error
          ? error.message
          : "This tool could not load. Try again.";
      retry.hidden = false;
      if (focus) focusHeading(feedbackHeading);
    }
  }
  function route(focus = true) {
    const { destination, unknown } = resolveDestination(location.hash);
    notice.hidden = !unknown;
    notice.textContent = unknown
      ? "That tool link is not available. Choose a tool below."
      : "";
    if (unknown) history.replaceState(null, "", "#/home");
    void show(destination, focus);
  }
  document.addEventListener("click", (event) => {
    if ((event.target as Element | null)?.closest(".skip-link")) {
      event.preventDefault();
      main.focus();
      main.scrollIntoView({ block: "start" });
      return;
    }
    const anchor = (event.target as Element | null)?.closest<HTMLAnchorElement>(
      "a[data-destination]",
    );
    if (
      !anchor ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    event.preventDefault();
    if (location.hash !== anchor.hash) history.pushState(null, "", anchor.hash);
    route();
  });
  window.addEventListener("hashchange", () => route());
  retry.addEventListener("click", () => void show(current, true));
  route(false);
}
