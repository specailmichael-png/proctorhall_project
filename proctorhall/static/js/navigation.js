(() => {
  const mobileNav = window.matchMedia("(max-width: 620px)");
  const compactAdminNav = window.matchMedia("(max-width: 900px)");
  const publicToggle = document.querySelector(".nav-toggle");
  const publicMenu = document.querySelector("#siteNavLinks");
  const adminToggle = document.querySelector(".sidebar-toggle");
  const adminMenu = document.querySelector("#adminSidebarMenu");

  const setExpanded = (toggle, menu, expanded, isCompact) => {
    if (!toggle || !menu) return;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", expanded ? "Close navigation menu" : "Open navigation menu");
    menu.hidden = isCompact && !expanded;
    const icon = toggle.querySelector("i");
    if (icon) {
      icon.classList.toggle("fa-bars", !expanded);
      icon.classList.toggle("fa-xmark", expanded);
    }
  };

  const syncMenus = () => {
    setExpanded(publicToggle, publicMenu, !mobileNav.matches, mobileNav.matches);
    setExpanded(adminToggle, adminMenu, !compactAdminNav.matches, compactAdminNav.matches);
  };

  for (const [toggle, menu, breakpoint] of [
    [publicToggle, publicMenu, mobileNav],
    [adminToggle, adminMenu, compactAdminNav],
  ]) {
    if (!toggle || !menu) continue;
    toggle.addEventListener("click", () => {
      setExpanded(toggle, menu, toggle.getAttribute("aria-expanded") !== "true", breakpoint.matches);
    });
    menu.addEventListener("click", (event) => {
      if (breakpoint.matches && event.target.closest("a")) setExpanded(toggle, menu, false, true);
    });
  }

  mobileNav.addEventListener("change", syncMenus);
  compactAdminNav.addEventListener("change", syncMenus);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (mobileNav.matches) setExpanded(publicToggle, publicMenu, false, true);
    if (compactAdminNav.matches) setExpanded(adminToggle, adminMenu, false, true);
  });

  syncMenus();
})();
