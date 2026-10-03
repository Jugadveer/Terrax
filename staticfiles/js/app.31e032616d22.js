/**
 * Progressive enhancement.
 *
 * Every feature below improves something that already works without it: the
 * theme falls back to the system setting, menus are links, forms submit
 * normally, and the marketplace filters are a plain GET form. Nothing here is
 * load bearing.
 */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* -------------------------------------------------------------------------
   Theme
   ------------------------------------------------------------------------- */

const THEME_KEY = "terrax-theme";

function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (e) {
    /* Private browsing. The server copy still holds. */
  }
  const token = csrfToken();
  if (token) {
    const body = new FormData();
    body.append("theme", theme);
    fetch("/theme/", { method: "POST", body, headers: { "X-CSRFToken": token } }).catch(
      () => {}
    );
  }
}

function initTheme() {
  $$("[data-theme-toggle]").forEach((button) =>
    button.addEventListener("click", () =>
      setTheme(currentTheme() === "dark" ? "light" : "dark")
    )
  );
}

/* -------------------------------------------------------------------------
   Menus and navigation
   ------------------------------------------------------------------------- */

function initMenus() {
  $$("[data-menu]").forEach((menu) => {
    const trigger = $("[data-menu-trigger]", menu);
    const panel = $("[data-menu-panel]", menu);
    if (!trigger || !panel) return;

    const close = () => {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    };

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = panel.hidden;
      $$("[data-menu-panel]").forEach((other) => (other.hidden = true));
      panel.hidden = !open;
      trigger.setAttribute("aria-expanded", String(open));
    });

    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
  });

  const navToggle = $("[data-nav-toggle]");
  const mobileNav = $("#mobile-nav");
  if (navToggle && mobileNav) {
    navToggle.addEventListener("click", () => {
      const open = mobileNav.hidden;
      mobileNav.hidden = !open;
      navToggle.setAttribute("aria-expanded", String(open));
      document.body.style.overflow = open ? "hidden" : "";
    });
  }
}

/* -------------------------------------------------------------------------
   Command palette
   ------------------------------------------------------------------------- */

function initPalette() {
  const dialog = $("#command-palette");
  if (!dialog) return;

  const input = $("#palette-query", dialog);
  const echo = $("[data-palette-echo]", dialog);

  const open = () => {
    if (dialog.open) return;
    dialog.showModal();
    input?.focus();
  };

  $$("[data-open-palette]").forEach((button) => button.addEventListener("click", open));

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      dialog.open ? dialog.close() : open();
    }
  });

  input?.addEventListener("input", () => {
    if (echo) echo.textContent = input.value.trim() || "anything";
  });
}

/* -------------------------------------------------------------------------
   Toasts
   ------------------------------------------------------------------------- */

function dismiss(toast) {
  toast.classList.add("is-leaving");
  toast.addEventListener("animationend", () => toast.remove(), { once: true });
}

function initToasts(root = document) {
  $$(".toast", root).forEach((toast) => {
    $("[data-dismiss-toast]", toast)?.addEventListener("click", () => dismiss(toast));
    setTimeout(() => toast.isConnected && dismiss(toast), 6000);
  });
}

export function toast(message, tone = "info") {
  const stack = $("#toasts");
  if (!stack) return;
  const element = document.createElement("div");
  element.className = `toast toast--${tone}`;
  element.innerHTML = `<p></p>`;
  $("p", element).textContent = message;
  stack.append(element);
  setTimeout(() => element.isConnected && dismiss(element), 6000);
}

/* -------------------------------------------------------------------------
   Scroll reveals
   ------------------------------------------------------------------------- */

function initReveals(root = document) {
  const targets = $$(".reveal:not(.is-visible)", root);
  if (!targets.length) return;

  const show = (element) => element.classList.add("is-visible");

  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    targets.forEach(show);
    return;
  }

  // Anything already on screen is shown at once. Fading in content the user is
  // looking at is not an entrance, it is a delay, and it leaves the page blank
  // if the observer is late.
  const pending = targets.filter((element) => {
    const box = element.getBoundingClientRect();
    if (box.top < innerHeight && box.bottom > 0) {
      show(element);
      return false;
    }
    return true;
  });
  if (!pending.length) return;

  // IntersectionObserver rather than a scroll listener: the browser does the
  // work off the main thread and nothing reflows while scrolling.
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry, index) => {
        if (!entry.isIntersecting) return;
        setTimeout(() => show(entry.target), index * 55);
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.05 }
  );

  pending.forEach((element) => observer.observe(element));
}

/* -------------------------------------------------------------------------
   File inputs
   ------------------------------------------------------------------------- */

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function initDropzones(root = document) {
  $$("[data-dropzone]", root).forEach((zone) => {
    const input = $('input[type="file"]', zone);
    const list = $("[data-file-list]", zone.parentElement) || null;
    if (!input) return;

    const render = () => {
      if (!list) return;
      list.innerHTML = "";
      [...input.files].forEach((file) => {
        const row = document.createElement("div");
        row.className = "file-chip";
        row.innerHTML = `<span class="name"></span><span class="size mono"></span>`;
        $(".name", row).textContent = file.name;
        $(".size", row).textContent = humanSize(file.size);
        list.append(row);
      });
    };

    input.addEventListener("change", render);

    ["dragenter", "dragover"].forEach((type) =>
      zone.addEventListener(type, (event) => {
        event.preventDefault();
        zone.classList.add("is-over");
      })
    );
    ["dragleave", "drop"].forEach((type) =>
      zone.addEventListener(type, () => zone.classList.remove("is-over"))
    );
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      input.files = event.dataTransfer.files;
      render();
    });
  });
}

/* -------------------------------------------------------------------------
   Galleries
   ------------------------------------------------------------------------- */

function initGallery(root = document) {
  $$("[data-gallery]", root).forEach((gallery) => {
    const main = $("[data-gallery-main]", gallery);
    if (!main) return;

    $$("[data-gallery-thumb]", gallery).forEach((thumb) => {
      thumb.addEventListener("click", () => {
        main.src = thumb.dataset.full || thumb.src;
        main.alt = thumb.alt;
        $$("[data-gallery-thumb]", gallery).forEach((other) =>
          other.setAttribute("aria-current", String(other === thumb))
        );
      });
    });
  });
}

/* -------------------------------------------------------------------------
   Forms
   ------------------------------------------------------------------------- */

function initForms(root = document) {
  // Stop the double submit that produces two identical offers.
  $$("form[data-guard]", root).forEach((form) => {
    form.addEventListener("submit", () => {
      const button = $('button[type="submit"]', form);
      if (button) {
        button.classList.add("is-busy");
        button.disabled = true;
        setTimeout(() => {
          button.disabled = false;
          button.classList.remove("is-busy");
        }, 8000);
      }
    });
  });

  $$("[data-confirm]", root).forEach((element) =>
    element.addEventListener("submit", (event) => {
      if (!confirm(element.dataset.confirm)) event.preventDefault();
    })
  );

  // Price fields: show the lakh/crore reading as the number is typed, because
  // a nine digit figure is unreadable without it.
  $$("[data-rupee-echo]", root).forEach((input) => {
    const output = $(input.dataset.rupeeEcho);
    if (!output) return;
    const update = () => {
      const value = Number(input.value);
      output.textContent = !value ? "" : formatRupees(value);
    };
    input.addEventListener("input", update);
    update();
  });

  $$("[data-copy]", root).forEach((button) =>
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy);
      const original = button.getAttribute("aria-label") || "";
      button.setAttribute("aria-label", "Copied");
      toast("Copied to the clipboard");
      setTimeout(() => button.setAttribute("aria-label", original), 2000);
    })
  );
}

export function formatRupees(value) {
  if (value >= 10000000) return `₹${(value / 10000000).toFixed(2).replace(/\.?0+$/, "")} Cr`;
  if (value >= 100000) return `₹${(value / 100000).toFixed(2).replace(/\.?0+$/, "")} L`;
  return `₹${value.toLocaleString("en-IN")}`;
}

/* -------------------------------------------------------------------------
   Compare tray
   ------------------------------------------------------------------------- */

function initCompare(root = document) {
  const tray = $("#compare-tray");
  const KEY = "terrax-compare";

  const read = () => {
    try {
      return JSON.parse(localStorage.getItem(KEY) || "[]");
    } catch (e) {
      return [];
    }
  };

  const write = (ids) => {
    try {
      localStorage.setItem(KEY, JSON.stringify(ids.slice(0, 4)));
    } catch (e) {}
    paint();
  };

  const paint = () => {
    const ids = read();
    $$("[data-compare]").forEach((button) =>
      button.setAttribute("aria-pressed", String(ids.includes(button.dataset.compare)))
    );
    if (!tray) return;
    tray.hidden = ids.length < 1;
    const count = $("[data-compare-count]", tray);
    if (count) count.textContent = ids.length;
    const link = $("[data-compare-link]", tray);
    if (link) link.href = `/properties/compare/?${ids.map((id) => `id=${id}`).join("&")}`;
  };

  $$("[data-compare]", root).forEach((button) =>
    button.addEventListener("click", () => {
      const id = button.dataset.compare;
      const ids = read();
      if (ids.includes(id)) {
        write(ids.filter((other) => other !== id));
      } else if (ids.length >= 4) {
        toast("Compare holds four properties at a time", "warning");
      } else {
        write([...ids, id]);
      }
    })
  );

  $("[data-compare-clear]")?.addEventListener("click", () => write([]));
  paint();
}

/* -------------------------------------------------------------------------
   Wiring
   ------------------------------------------------------------------------- */

function csrfToken() {
  return $('[name="csrfmiddlewaretoken"]')?.value || readCookie("csrftoken");
}

function readCookie(name) {
  const match = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
  return match ? decodeURIComponent(match[2]) : "";
}

function enhance(root = document) {
  initReveals(root);
  initDropzones(root);
  initGallery(root);
  initForms(root);
  initCompare(root);
  initToasts(root);
}

initTheme();
initMenus();
initPalette();
enhance();

// HTMX swaps markup in without a page load, so anything enhanced has to be
// enhanced again on the fragment that arrived.
document.body.addEventListener("htmx:afterSwap", (event) => enhance(event.target));
