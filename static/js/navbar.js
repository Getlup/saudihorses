// ============================================================
// شريط التنقل: قوائم منسدلة، تقليص عند التمرير، تمييز الصفحة النشطة،
// ظهور العناصر عند التمرير، وعدّاد أرقام حقيقي (القيم تأتي من data-count-to
// المُعبَّأة من الخادم بأرقام فعلية — لا أرقام وهمية هنا).
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
  initHeaderScroll();
  initDropdowns();
  initActiveLink();
  initReveal();
  initCounters();
  initLiveClock();
});

// ---------- ساعة وتاريخ حيّان بشريط التنقل (Asia/Riyadh) ----------
// تحويل يدوي احتياطي: أي رقم هندي-عربي (٠-٩) يُستبدل برقم لاتيني عادي (0-9)، بغض النظر
// عن سلوك المتصفح مع numberingSystem — طبقة حماية إضافية بعد تكرار هذي المشكلة أكثر من مرة.
const EASTERN_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩";
function forceLatinDigits(str) {
  return str.replace(/[٠-٩]/g, (d) => String(EASTERN_ARABIC_DIGITS.indexOf(d)));
}

function initLiveClock() {
  const el = document.getElementById("live-clock");
  if (!el) return;
  const timeEl = el.querySelector(".clock-time");
  const dateEl = el.querySelector(".clock-date");
  if (!timeEl || !dateEl) return;

  const locale = el.getAttribute("data-locale") || "ar-SA";
  const timeFmt = new Intl.DateTimeFormat(locale, {
    hour: "2-digit", minute: "2-digit", timeZone: "Asia/Riyadh", numberingSystem: "latn"
  });
  const dateFmt = new Intl.DateTimeFormat(locale, {
    day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Riyadh", numberingSystem: "latn"
  });
  const update = () => {
    const now = new Date();
    timeEl.textContent = forceLatinDigits(timeFmt.format(now));
    dateEl.textContent = forceLatinDigits(dateFmt.format(now));
  };
  update();
  setInterval(update, 30000);
}

// ---------- تقليص الشريط عند التمرير ----------
function initHeaderScroll() {
  const header = document.querySelector("[data-site-header]");
  if (!header) return;
  const update = () => header.classList.toggle("is-scrolled", window.scrollY > 40);
  window.addEventListener("scroll", update, { passive: true });
  update();
}

// ---------- القوائم المنسدلة ----------
function initDropdowns() {
  const dropdowns = document.querySelectorAll("[data-dropdown]");

  const closeAll = (except = null) => {
    dropdowns.forEach((dropdown) => {
      if (dropdown === except) return;
      dropdown.classList.remove("is-open");
      dropdown.querySelector("[data-dropdown-trigger]")?.setAttribute("aria-expanded", "false");
    });
  };

  dropdowns.forEach((dropdown) => {
    const trigger = dropdown.querySelector("[data-dropdown-trigger]");
    const menu = dropdown.querySelector("[data-dropdown-menu]");
    if (!trigger || !menu) return;

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = !dropdown.classList.contains("is-open");
      closeAll(dropdown);
      dropdown.classList.toggle("is-open", willOpen);
      trigger.setAttribute("aria-expanded", String(willOpen));
    });

    menu.addEventListener("click", (event) => event.stopPropagation());
  });

  document.addEventListener("click", () => closeAll());

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = document.querySelector("[data-dropdown].is-open");
    const openTrigger = open?.querySelector("[data-dropdown-trigger]");
    closeAll();
    openTrigger?.focus();
  });
}

// ---------- تمييز الرابط النشط بحسب مسار الصفحة الحالي ----------
function initActiveLink() {
  const current = window.location.pathname;
  document.querySelectorAll("[data-nav-link]").forEach((link) => {
    try {
      const linkPath = new URL(link.href).pathname;
      if (linkPath === current || (linkPath !== "/" && current.startsWith(linkPath))) {
        link.classList.add("is-active");
      }
    } catch (e) { /* روابط غير مطلقة — تجاهل */ }
  });
}

// ---------- ظهور العناصر عند التمرير ----------
function initReveal() {
  const elements = document.querySelectorAll("[data-reveal], .stat-card");
  if (!elements.length) return;

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    elements.forEach((el) => el.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry, i) => {
        if (!entry.isIntersecting) return;
        setTimeout(() => entry.target.classList.add("is-visible"), i * 70);
        obs.unobserve(entry.target);
      });
    },
    { threshold: 0.15, rootMargin: "0px 0px -40px 0px" }
  );
  elements.forEach((el) => observer.observe(el));
}

// ---------- عدّاد الأرقام — يعرض القيم الحقيقية القادمة من الخادم فقط ----------
// كل عنصر [data-count-to] يحمل الرقم الفعلي (مثال: عدد الخيول الحقيقي بقاعدة البيانات)
// كما أرسله القالب (Jinja) من الخادم. الحركة هنا بصرية فقط ولا تُغيّر أو تُخترع القيمة.
function initCounters() {
  const counters = document.querySelectorAll("[data-count-to]");
  if (!counters.length) return;

  const animate = (el) => {
    const target = Number(el.getAttribute("data-count-to"));
    if (!Number.isFinite(target)) return;

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.textContent = target.toLocaleString("en-US");
      return;
    }

    const duration = 800;
    const start = performance.now();
    const step = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out
      el.textContent = Math.round(target * eased).toLocaleString("en-US");
      if (progress < 1) requestAnimationFrame(step);
      else el.textContent = target.toLocaleString("en-US");
    };
    requestAnimationFrame(step);
  };

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        animate(entry.target);
        obs.unobserve(entry.target);
      });
    },
    { threshold: 0.3 }
  );
  counters.forEach((el) => observer.observe(el));
}
