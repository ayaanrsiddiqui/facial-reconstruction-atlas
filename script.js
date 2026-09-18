const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");
const clearBtn = document.getElementById("clear-btn");
const statusMessage = document.getElementById("status-message");
const resultsGrid = document.getElementById("results-grid");
const resultsCount = document.getElementById("results-count");
const sortWrap = document.getElementById("sort-wrap");
const sortSelect = document.getElementById("sort-select");
const diagramsRoot = document.getElementById("diagrams-root");
const anatomyBackBtn = document.getElementById("anatomy-back-btn");

const filterRegion = document.getElementById("filter-region");
const filterSubLocation = document.getElementById("filter-sub-location");
const filterFullThickness = document.getElementById("filter-full-thickness");
const filterMethod = document.getElementById("filter-method");
const filterGeneralFlap = document.getElementById("filter-general-flap");
const filterSpecificFlap = document.getElementById("filter-specific-flap");
const filterGraft = document.getElementById("filter-graft");
const filterGraftDonor = document.getElementById("filter-graft-donor");

const filterElements = [
  filterRegion,
  filterSubLocation,
  filterFullThickness,
  filterMethod,
  filterGeneralFlap,
  filterSpecificFlap,
  filterGraft,
  filterGraftDonor,
];

const filterFullThicknessWrap = document.getElementById("filter-full-thickness-wrap");
const filterGeneralFlapWrap = document.getElementById("filter-general-flap-wrap");
const filterSpecificFlapWrap = document.getElementById("filter-specific-flap-wrap");
const filterGraftWrap = document.getElementById("filter-graft-wrap");
const filterGraftDonorWrap = document.getElementById("filter-graft-donor-wrap");

const authArea = document.getElementById("auth-area");
const authModal = document.getElementById("auth-modal");
const authModalTitle = document.getElementById("auth-modal-title");
const authModalClose = document.getElementById("auth-modal-close");
const authForm = document.getElementById("auth-form");
const authUsername = document.getElementById("auth-username");
const authPassword = document.getElementById("auth-password");
const authError = document.getElementById("auth-error");
const authSubmit = document.getElementById("auth-submit");
const authSwitch = document.getElementById("auth-switch");
const authSwitchText = document.getElementById("auth-switch-text");
const authSwitchBtn = document.getElementById("auth-switch-btn");

const caseModal = document.getElementById("case-modal");
const caseModalClose = document.getElementById("case-modal-close");
const caseModalCarousel = document.getElementById("case-modal-carousel");
const caseModalPrev = document.getElementById("case-modal-prev");
const caseModalNext = document.getElementById("case-modal-next");
const caseModalDots = document.getElementById("case-modal-dots");
const caseModalFavorite = document.getElementById("case-modal-favorite");
const caseModalLocation = document.getElementById("case-modal-location");
const caseModalSize = document.getElementById("case-modal-size");
const caseModalRepair = document.getElementById("case-modal-repair");
const caseModalAddon = document.getElementById("case-modal-addon");
const caseModalNotes = document.getElementById("case-modal-notes");
const caseModalCommentsHeading = document.getElementById("case-modal-comments-heading");
const caseModalCommentList = document.getElementById("case-modal-comment-list");
const caseModalCommentFormSlot = document.getElementById("case-modal-comment-form-slot");

let currentUser = null;
let authMode = "login";
let favoritesViewActive = false;
let resultsByFolder = new Map();
let activeModalFolder = null;
let modalDotObserver = null;
let lastPayload = null;
let currentSort = "default";

const SORT_COMPARATORS = {
  location: (a, b) =>
    (a.location_display || "").localeCompare(b.location_display || ""),
  comments: (a, b) => (b.comment_count || 0) - (a.comment_count || 0),
};

function applySort(cases) {
  const comparator = SORT_COMPARATORS[currentSort];
  return comparator ? [...cases].sort(comparator) : cases;
}

const FULL_THICKNESS_REGIONS = new Set(["Nose", "Periorbital", "Ear", "Lip"]);
const FLAP_METHODS = new Set(["Local flap", "Regional flap", "Free flap"]);
const GRAFT_METHODS = new Set(["Graft"]);

function setFilterVisible(select, wrap, visible) {
  wrap.hidden = !visible;
  if (!visible && select.value) {
    select.value = "";
  }
}

function updateConditionalFilterVisibility() {
  const method = filterMethod.value;
  // Full-thickness applies to flap repairs of nose/ear/lid/lip — not graft-only repairs.
  const showFullThickness =
    FULL_THICKNESS_REGIONS.has(filterRegion.value) && !GRAFT_METHODS.has(method);
  setFilterVisible(filterFullThickness, filterFullThicknessWrap, showFullThickness);

  const showFlapFilters = FLAP_METHODS.has(method);
  const showGraftFilters = GRAFT_METHODS.has(method);

  setFilterVisible(filterGeneralFlap, filterGeneralFlapWrap, showFlapFilters);
  setFilterVisible(filterSpecificFlap, filterSpecificFlapWrap, showFlapFilters);
  setFilterVisible(filterGraft, filterGraftWrap, showGraftFilters);
  setFilterVisible(filterGraftDonor, filterGraftDonorWrap, showGraftFilters);
}

let filterConfig = {};
let anatomyConfig = {};
let activeDiagram = "face";

function setStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.classList.toggle("error", isError);
}

function excludeNA(values) {
  return (values || []).filter((value) => value.trim().toLowerCase() !== "n/a");
}

function capitalizeLabel(value) {
  return value ? value.replace(/\b\w/g, (char) => char.toUpperCase()) : value;
}

function populateSelect(select, items, placeholder) {
  const current = select.value;
  select.innerHTML = `<option value="">${placeholder}</option>`;
  items.forEach((item) => {
    const value = typeof item === "string" ? item : item.value;
    const label = typeof item === "string" ? item : item.label;
    const option = document.createElement("option");
    option.value = value;
    option.textContent = capitalizeLabel(label);
    select.appendChild(option);
  });
  if ([...select.options].some((option) => option.value === current)) {
    select.value = current;
  }
}

function getSubLocationOptions(region) {
  if (region && filterConfig.sub_locations?.[region]) {
    return filterConfig.sub_locations[region].map((sub) => ({ value: sub, label: sub }));
  }

  const subLocationsByRegion = filterConfig.sub_locations || {};
  const options = [];
  Object.entries(subLocationsByRegion).forEach(([regionName, subs]) => {
    subs.forEach((sub) => {
      options.push({ value: sub, label: `${regionName} (${sub.toLowerCase()})` });
    });
  });
  return options;
}

function updateSubLocationOptions() {
  populateSelect(
    filterSubLocation,
    getSubLocationOptions(filterRegion.value),
    "All sub-locations"
  );
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    const error = new Error(`Request failed: ${url}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function getFilterValues() {
  return {
    q: searchInput.value.trim(),
    region: filterRegion.value,
    sub_location: filterSubLocation.value,
    full_thickness: filterFullThickness.value,
    method_of_repair: filterMethod.value,
    general_flap: filterGeneralFlap.value,
    specific_flap_description: filterSpecificFlap.value,
    graft: filterGraft.value,
    graft_donor_site: filterGraftDonor.value,
  };
}

function buildSearchUrl(filters = getFilterValues()) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) {
      params.set(key, value);
    }
  });
  const query = params.toString();
  return query ? `/api/search?${query}` : "/api/search";
}

function applyFilterMap(filterMap = {}) {
  if (Object.prototype.hasOwnProperty.call(filterMap, "region")) {
    filterRegion.value = filterMap.region || "";
    updateSubLocationOptions();
  }
  if (Object.prototype.hasOwnProperty.call(filterMap, "sub_location")) {
    filterSubLocation.value = filterMap.sub_location || "";
  }
  updateConditionalFilterVisibility();
  syncExplorerToFilters();
}

function syncExplorerToFilters() {
  if (!anatomyConfig.face) {
    return;
  }

  const region = filterRegion.value;
  const subLocation = filterSubLocation.value;

  const faceRegionEntry = region
    ? anatomyConfig.face.regions.find((entry) => entry.filter?.region === region)
    : null;
  const diagramKey = faceRegionEntry?.detail_diagram || "face";

  showDiagram(diagramKey);

  if (diagramKey === "face") {
    if (faceRegionEntry) {
      document
        .querySelectorAll(`.anatomy-region[data-region-id="${faceRegionEntry.id}"]`)
        .forEach((node) => node.classList.add("is-selected"));
    }
    return;
  }

  if (subLocation) {
    const subEntry = anatomyConfig[diagramKey]?.regions.find(
      (entry) => entry.filter?.sub_location === subLocation
    );
    if (subEntry) {
      document
        .querySelectorAll(`.anatomy-region[data-region-id="${subEntry.id}"]`)
        .forEach((node) => node.classList.add("is-selected"));
    }
  }
}

function showDiagram(diagramKey) {
  activeDiagram = diagramKey;
  document.querySelectorAll("[data-diagram]").forEach((node) => {
    node.hidden = node.dataset.diagram !== diagramKey;
  });
  if (anatomyBackBtn) {
    anatomyBackBtn.hidden = diagramKey === "face";
  }
  document.querySelectorAll(".anatomy-region.is-selected").forEach((node) => {
    node.classList.remove("is-selected");
  });
}

function findRegionConfig(regionId) {
  const diagram = anatomyConfig[activeDiagram];
  if (!diagram) {
    return null;
  }
  return diagram.regions.find((region) => region.id === regionId) || null;
}

function isMeaningful(value) {
  return Boolean(value && value.trim() && value.trim().toLowerCase() !== "n/a");
}

function isFullThickness(caseRecord) {
  return (caseRecord.full_thickness || "").trim().toLowerCase() === "yes";
}

function joinEmDash(parts) {
  return parts.filter(isMeaningful).join(" — ");
}

function renderSizeLine(caseRecord) {
  const size = isMeaningful(caseRecord.defect_size) ? caseRecord.defect_size.trim() : "";
  if (size && isFullThickness(caseRecord)) {
    return `${size}, full thickness`;
  }
  if (size) {
    return size;
  }
  if (isFullThickness(caseRecord)) {
    return "Full thickness";
  }
  return "";
}

function isFlapRepair(caseRecord) {
  return (caseRecord.method_of_repair || "").toLowerCase().includes("flap");
}

function isGraftRepair(caseRecord) {
  return (caseRecord.method_of_repair || "").trim().toLowerCase() === "graft";
}

function renderRepairLine(caseRecord) {
  if (isFlapRepair(caseRecord)) {
    return joinEmDash([
      caseRecord.method_of_repair,
      caseRecord.general_flap,
      caseRecord.specific_flap_description,
    ]);
  }

  if (isGraftRepair(caseRecord)) {
    return joinEmDash([
      caseRecord.method_of_repair,
      caseRecord.graft,
      caseRecord.graft_donor_site,
    ]);
  }

  return joinEmDash([
    caseRecord.method_of_repair,
    caseRecord.general_flap,
    caseRecord.specific_flap_description,
    caseRecord.graft,
    caseRecord.graft_donor_site,
  ]);
}

function renderRepairDetailLine(caseRecord) {
  if (isFlapRepair(caseRecord)) {
    return joinEmDash([caseRecord.general_flap, caseRecord.specific_flap_description]);
  }

  if (isGraftRepair(caseRecord)) {
    return joinEmDash([caseRecord.graft, caseRecord.graft_donor_site]);
  }

  return joinEmDash([
    caseRecord.general_flap,
    caseRecord.specific_flap_description,
    caseRecord.graft,
    caseRecord.graft_donor_site,
  ]);
}

function renderGraftAddonLine(caseRecord) {
  if (!isFlapRepair(caseRecord)) {
    return "";
  }

  const graftDetails = joinEmDash([caseRecord.graft, caseRecord.graft_donor_site]);
  if (!graftDetails) {
    return "";
  }

  return `(Graft — ${graftDetails})`;
}

function renderFlapAddonLine(caseRecord) {
  if (!isGraftRepair(caseRecord)) {
    return "";
  }

  const flapDetails = joinEmDash([
    caseRecord.general_flap,
    caseRecord.specific_flap_description,
  ]);
  if (!flapDetails) {
    return "";
  }

  return `(${flapDetails})`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

let demoMode = false;
// Whether the server will accept POST /api/auth/register at all. Closed by
// default, so assume closed until /api/auth/me says otherwise.
let registrationOpen = false;

async function refreshAuthState() {
  try {
    const data = await fetchJson("/api/auth/me");
    currentUser = data.username || null;
    demoMode = Boolean(data.demo);
    registrationOpen = Boolean(data.registration_open);
  } catch (error) {
    currentUser = null;
    demoMode = false;
    registrationOpen = false;
  }
  renderDemoBanner();
  renderAuthArea();
}

function renderDemoBanner() {
  const banner = document.getElementById("demo-banner");
  if (banner) banner.hidden = !demoMode;
  // "Internal Use Only" is true of the internal deployment, not of the public demo.
  const badge = document.getElementById("internal-badge");
  if (badge) badge.hidden = demoMode;
}

function renderAuthArea() {
  authArea.innerHTML = "";

  if (currentUser) {
    const label = document.createElement("span");
    label.className = "auth-username";
    label.textContent = `Signed in as ${currentUser}`;

    const favoritesLink = document.createElement("button");
    favoritesLink.type = "button";
    favoritesLink.className = "secondary-btn auth-btn";
    favoritesLink.textContent = "My Favorites";
    favoritesLink.addEventListener("click", showFavorites);

    const logoutBtn = document.createElement("button");
    logoutBtn.type = "button";
    logoutBtn.className = "secondary-btn auth-btn";
    logoutBtn.textContent = "Log out";
    logoutBtn.addEventListener("click", handleLogout);

    authArea.append(label, favoritesLink, logoutBtn);
  } else if (demoMode) {
    // Demo: browsing and favorites are open; signing in is optional and unlocks comments.
    const note = document.createElement("span");
    note.className = "auth-username";
    note.textContent = "Demo — sample data";

    const demoAuthBtn = document.createElement("button");
    demoAuthBtn.type = "button";
    demoAuthBtn.className = "secondary-btn auth-btn";
    demoAuthBtn.textContent = registrationOpen ? "Create demo account" : "Sign in";
    demoAuthBtn.addEventListener("click", () =>
      openAuthModal(registrationOpen ? "register" : "login")
    );

    authArea.append(note, demoAuthBtn);
  } else {
    const loginBtn = document.createElement("button");
    loginBtn.type = "button";
    loginBtn.className = "secondary-btn auth-btn";
    loginBtn.textContent = "Sign in";
    loginBtn.addEventListener("click", () => openAuthModal("login"));

    authArea.append(loginBtn);

    // No Register button when the server has registration closed — accounts are
    // created on the host with create_account.py until SSO lands.
    if (registrationOpen) {
      const registerBtn = document.createElement("button");
      registerBtn.type = "button";
      registerBtn.className = "secondary-btn auth-btn";
      registerBtn.textContent = "Register";
      registerBtn.addEventListener("click", () => openAuthModal("register"));
      authArea.append(registerBtn);
    }
  }
}

function openAuthModal(mode) {
  authMode = mode;
  authError.hidden = true;
  authForm.reset();
  authModalTitle.textContent = mode === "login" ? "Sign in" : "Create an account";
  authSubmit.textContent = mode === "login" ? "Sign in" : "Create account";
  authSwitchText.textContent =
    mode === "login" ? "Don't have an account?" : "Already have an account?";
  authSwitchBtn.textContent = mode === "login" ? "Create one" : "Sign in";
  authSwitch.hidden = !registrationOpen;
  authModal.hidden = false;
  authUsername.focus();
}

function closeAuthModal() {
  authModal.hidden = true;
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const username = authUsername.value.trim();
  const password = authPassword.value;

  authError.hidden = true;
  authSubmit.disabled = true;

  try {
    const endpoint = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Something went wrong.");
    }
    currentUser = data.username;
    renderAuthArea();
    closeAuthModal();
    await loadConfig();
    runSearch();
  } catch (error) {
    authError.textContent = error.message;
    authError.hidden = false;
  } finally {
    authSubmit.disabled = false;
  }
}

async function handleLogout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (error) {
    console.error(error);
  }
  currentUser = null;
  renderAuthArea();
  runSearch();
}

async function showFavorites() {
  favoritesViewActive = true;
  setStatus("Loading your favorites…");
  renderSkeletonCards();
  try {
    const payload = await fetchJson("/api/favorites");
    renderResults(payload);
    setStatus(`Showing ${payload.count} favorited case${payload.count === 1 ? "" : "s"}.`);
  } catch (error) {
    console.error(error);
    setStatus("Unable to load favorites.", true);
  }
}

function cssEscape(value) {
  return window.CSS && CSS.escape ? CSS.escape(value) : value;
}

function applyFavoriteState(folderName, favorited) {
  const record = resultsByFolder.get(folderName);
  if (record) {
    record.favorited = favorited;
  }

  document
    .querySelectorAll(`.case-favorite-btn[data-folder-name="${cssEscape(folderName)}"]`)
    .forEach((btn) => {
      btn.classList.toggle("is-favorited", favorited);
      btn.textContent = favorited ? "★" : "☆";
      btn.setAttribute("aria-pressed", favorited ? "true" : "false");
      btn.setAttribute("aria-label", favorited ? "Remove from favorites" : "Add to favorites");
    });

  if (favoritesViewActive && !favorited) {
    resultsGrid.querySelector(`.case-card[data-folder-name="${cssEscape(folderName)}"]`)?.remove();
    if (activeModalFolder === folderName) {
      closeCaseModal();
    }
  }
}

async function toggleFavorite(folderName, button) {
  if (!currentUser) {
    openAuthModal("login");
    return;
  }

  button.disabled = true;
  try {
    const response = await fetch(`/api/cases/${encodeURIComponent(folderName)}/favorite`, {
      method: "POST",
    });
    if (!response.ok) {
      throw new Error("Unable to update favorite.");
    }
    const data = await response.json();
    applyFavoriteState(folderName, data.favorited);
  } catch (error) {
    console.error(error);
  } finally {
    button.disabled = false;
  }
}

function formatCommentDate(value) {
  if (!value) {
    return "";
  }
  const date = new Date(`${value.replace(" ", "T")}Z`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function renderCommentItemHtml(comment) {
  return `
    <div class="comment-item">
      <p class="comment-meta"><strong>${escapeHtml(comment.username)}</strong> · ${formatCommentDate(comment.created_at)}</p>
      <p class="comment-body">${escapeHtml(comment.body)}</p>
    </div>
  `;
}

function renderModalCommentForm(folderName) {
  if (!currentUser) {
    caseModalCommentFormSlot.innerHTML = `
      <p class="comment-login-hint">
        <button type="button" class="link-btn comment-login-btn">Log in</button> to post a comment.
      </p>
    `;
    return;
  }
  caseModalCommentFormSlot.innerHTML = `
    <form class="comment-form" data-folder-name="${escapeHtml(folderName)}">
      <textarea class="comment-input" placeholder="Share a note about this case…" maxlength="2000" required></textarea>
      <p class="comment-error" hidden></p>
      <button type="submit" class="comment-submit">Post comment</button>
    </form>
  `;
}

function updateCardCommentCount(folderName, count) {
  const record = resultsByFolder.get(folderName);
  if (record) {
    record.comment_count = count;
  }
  const card = resultsGrid.querySelector(`.case-card[data-folder-name="${cssEscape(folderName)}"]`);
  const btn = card?.querySelector(".case-comments-btn");
  if (btn) {
    btn.textContent = `💬 Comments${count ? ` (${count})` : ""}`;
  }
}

async function loadModalComments(folderName) {
  try {
    const data = await fetchJson(`/api/cases/${encodeURIComponent(folderName)}/comments`);
    if (activeModalFolder !== folderName) {
      return;
    }
    caseModalCommentList.innerHTML = data.comments.length
      ? data.comments.map(renderCommentItemHtml).join("")
      : `<p class="comment-empty">No comments yet.</p>`;
    caseModalCommentsHeading.textContent = `Comments${data.count ? ` (${data.count})` : ""}`;
    renderModalCommentForm(folderName);
    updateCardCommentCount(folderName, data.count);
  } catch (error) {
    console.error(error);
    if (activeModalFolder === folderName) {
      caseModalCommentList.innerHTML = `<p class="comment-empty">Unable to load comments.</p>`;
    }
  }
}

async function handleCommentSubmit(form) {
  const folderName = form.dataset.folderName;
  const textarea = form.querySelector(".comment-input");
  const errorEl = form.querySelector(".comment-error");
  const submitBtn = form.querySelector(".comment-submit");
  const body = textarea.value.trim();

  errorEl.hidden = true;
  if (!body) {
    return;
  }

  submitBtn.disabled = true;
  try {
    const response = await fetch(`/api/cases/${encodeURIComponent(folderName)}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || "Unable to post comment.");
    }

    const emptyMessage = caseModalCommentList.querySelector(".comment-empty");
    if (emptyMessage) {
      emptyMessage.remove();
    }
    caseModalCommentList.insertAdjacentHTML("beforeend", renderCommentItemHtml(data));
    caseModalCommentList.scrollTop = caseModalCommentList.scrollHeight;
    textarea.value = "";

    const newCount = caseModalCommentList.querySelectorAll(".comment-item").length;
    caseModalCommentsHeading.textContent = `Comments${newCount ? ` (${newCount})` : ""}`;
    updateCardCommentCount(folderName, newCount);
  } catch (error) {
    errorEl.textContent = error.message;
    errorEl.hidden = false;
  } finally {
    submitBtn.disabled = false;
  }
}

function setModalText(el, text) {
  if (text) {
    el.textContent = text;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

function updateActiveDot(index) {
  caseModalDots.querySelectorAll(".carousel-dot").forEach((dot, i) => {
    dot.classList.toggle("is-active", i === index);
  });
}

function setupModalDotObserver(slideCount) {
  if (modalDotObserver) {
    modalDotObserver.disconnect();
    modalDotObserver = null;
  }
  if (slideCount <= 1) {
    return;
  }
  const slides = [...caseModalCarousel.querySelectorAll(".carousel-slide")];
  modalDotObserver = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          updateActiveDot(slides.indexOf(entry.target));
        }
      });
    },
    { root: caseModalCarousel, threshold: 0.6 }
  );
  slides.forEach((slide) => modalDotObserver.observe(slide));
}

function scrollCarouselTo(index) {
  const slide = caseModalCarousel.children[index];
  if (slide) {
    caseModalCarousel.scrollTo({ left: slide.offsetLeft, behavior: "smooth" });
  }
}

function scrollCarouselBy(direction) {
  caseModalCarousel.scrollBy({ left: caseModalCarousel.clientWidth * direction, behavior: "smooth" });
}

function renderModalCarousel(images, patientId) {
  caseModalCarousel.innerHTML = images
    .map(
      (image) => `
        <div class="carousel-slide">
          <img src="${image.url}" alt="Clinical photo for ${escapeHtml(patientId)}" />
        </div>
      `
    )
    .join("");
  caseModalCarousel.scrollLeft = 0;

  const showNav = images.length > 1;
  caseModalPrev.hidden = !showNav;
  caseModalNext.hidden = !showNav;
  caseModalDots.innerHTML = showNav
    ? images
        .map(
          (_, i) =>
            `<button type="button" class="carousel-dot${i === 0 ? " is-active" : ""}" data-index="${i}" aria-label="Image ${i + 1}"></button>`
        )
        .join("")
    : "";
  setupModalDotObserver(images.length);
}

function renderCaseModal(caseRecord) {
  activeModalFolder = caseRecord.folder_name;

  renderModalCarousel([{ url: caseRecord.image_url }], caseRecord.patient_id);

  const location =
    caseRecord.location_display || `${caseRecord.region} (${caseRecord.sub_location})`;
  caseModalLocation.textContent = location;
  setModalText(caseModalSize, renderSizeLine(caseRecord));
  setModalText(caseModalRepair, renderRepairLine(caseRecord));
  setModalText(
    caseModalAddon,
    renderGraftAddonLine(caseRecord) || renderFlapAddonLine(caseRecord)
  );
  setModalText(caseModalNotes, isMeaningful(caseRecord.notes) ? caseRecord.notes.trim() : "");

  const isFavorited = Boolean(caseRecord.favorited);
  caseModalFavorite.dataset.folderName = caseRecord.folder_name;
  caseModalFavorite.classList.toggle("is-favorited", isFavorited);
  caseModalFavorite.textContent = isFavorited ? "★" : "☆";
  caseModalFavorite.setAttribute("aria-pressed", isFavorited ? "true" : "false");
  caseModalFavorite.setAttribute(
    "aria-label",
    isFavorited ? "Remove from favorites" : "Add to favorites"
  );

  caseModalCommentsHeading.textContent = "Comments";
  caseModalCommentList.innerHTML = `<p class="comment-empty">Loading comments…</p>`;
  caseModalCommentFormSlot.innerHTML = "";
  loadModalComments(caseRecord.folder_name);
}

async function loadCaseImages(folderName, patientId) {
  try {
    const detail = await fetchJson(`/api/cases/${encodeURIComponent(folderName)}`);
    if (activeModalFolder !== folderName) {
      return;
    }
    const images = detail.images?.length ? detail.images : [{ url: detail.image_url }];
    renderModalCarousel(images, patientId);
  } catch (error) {
    console.error(error);
  }
}

function openCaseModal(folderName) {
  const caseRecord = resultsByFolder.get(folderName);
  if (!caseRecord) {
    return;
  }
  renderCaseModal(caseRecord);
  caseModal.hidden = false;
  loadCaseImages(folderName, caseRecord.patient_id);
}

function closeCaseModal() {
  caseModal.hidden = true;
  activeModalFolder = null;
  if (modalDotObserver) {
    modalDotObserver.disconnect();
    modalDotObserver = null;
  }
}

function renderCaseCard(caseRecord) {
  const card = document.createElement("article");
  card.className = "case-card";
  card.dataset.folderName = caseRecord.folder_name;
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `View case ${caseRecord.patient_id}`);

  const folderName = caseRecord.folder_name;
  const location =
    caseRecord.location_display || `${caseRecord.region} (${caseRecord.sub_location})`;
  const sizeLine = renderSizeLine(caseRecord);
  const method = isMeaningful(caseRecord.method_of_repair)
    ? caseRecord.method_of_repair.trim()
    : "";
  const repairDetailLine = renderRepairDetailLine(caseRecord);
  const graftAddonLine = renderGraftAddonLine(caseRecord);
  const flapAddonLine = renderFlapAddonLine(caseRecord);
  const addonLine = graftAddonLine || flapAddonLine;
  const notes = isMeaningful(caseRecord.notes) ? caseRecord.notes.trim() : "";
  const isFavorited = Boolean(caseRecord.favorited);
  const commentCount = caseRecord.comment_count || 0;

  card.innerHTML = `
    <div class="case-image-wrap">
      <img
        src="${caseRecord.image_url}"
        alt="Clinical photo for ${escapeHtml(caseRecord.patient_id)}"
        loading="lazy"
      />
      <button
        type="button"
        class="case-favorite-btn${isFavorited ? " is-favorited" : ""}"
        data-folder-name="${escapeHtml(folderName)}"
        aria-pressed="${isFavorited ? "true" : "false"}"
        aria-label="${isFavorited ? "Remove from favorites" : "Add to favorites"}"
      >${isFavorited ? "★" : "☆"}</button>
    </div>
    <div class="case-meta">
      <p class="case-location">${escapeHtml(location)}</p>
      ${method ? `<span class="case-tag">${escapeHtml(method)}</span>` : ""}
      ${repairDetailLine ? `<p class="case-repair">${escapeHtml(repairDetailLine)}</p>` : ""}
      ${addonLine ? `<p class="case-graft-addon">${escapeHtml(addonLine)}</p>` : ""}
      ${sizeLine ? `<p class="case-size">${escapeHtml(sizeLine)}</p>` : ""}
      ${
        notes
          ? `<div class="case-pearls">
              <div class="case-pearls-panel">
                <p class="case-pearls-text">${escapeHtml(notes)}</p>
                <button type="button" class="case-pearls-toggle" hidden aria-expanded="false">More</button>
              </div>
            </div>`
          : ""
      }
      <div class="case-card-footer">
        <button type="button" class="case-comments-btn">
          <span aria-hidden="true">💬</span> ${commentCount || "Comment"}
        </button>
      </div>
    </div>
  `;

  return card;
}

function syncPearlsToggles() {
  resultsGrid.querySelectorAll(".case-pearls").forEach((pearls) => {
    const text = pearls.querySelector(".case-pearls-text");
    const toggle = pearls.querySelector(".case-pearls-toggle");
    if (!text || !toggle) {
      return;
    }

    const card = pearls.closest(".case-card");
    if (card?.classList.contains("is-pearls-open")) {
      toggle.hidden = false;
      return;
    }

    // Measure unconstrained height vs single-line clamp.
    text.classList.add("is-measuring");
    const fullHeight = text.scrollHeight;
    text.classList.remove("is-measuring");
    const clampedHeight = text.clientHeight;
    toggle.hidden = fullHeight <= clampedHeight + 1;
  });
}

function renderCaseList(cases) {
  resultsGrid.innerHTML = "";
  resultsByFolder.clear();

  const fragment = document.createDocumentFragment();
  cases.forEach((caseRecord) => {
    resultsByFolder.set(caseRecord.folder_name, caseRecord);
    fragment.appendChild(renderCaseCard(caseRecord));
  });
  resultsGrid.appendChild(fragment);
  syncPearlsToggles();
}

function renderSkeletonCards(count = 6) {
  resultsGrid.innerHTML = Array.from({ length: count })
    .map(
      () => `
        <div class="case-card-skeleton">
          <div class="skeleton-block skeleton-image"></div>
          <div class="skeleton-lines">
            <div class="skeleton-block skeleton-line" style="width: 70%"></div>
            <div class="skeleton-block skeleton-line" style="width: 40%"></div>
            <div class="skeleton-block skeleton-line" style="width: 88%"></div>
          </div>
        </div>
      `
    )
    .join("");
}

function renderResults(payload) {
  lastPayload = payload;
  resultsCount.textContent = `${payload.count} case${payload.count === 1 ? "" : "s"}`;
  sortWrap.hidden = payload.count === 0;

  if (payload.count === 0) {
    resultsGrid.innerHTML = "";
    resultsByFolder.clear();
    setStatus("No matching cases found. Try another region or broaden your search.");
    return;
  }

  renderCaseList(applySort(payload.cases));
  setStatus(`Showing ${payload.count} matching case${payload.count === 1 ? "" : "s"}.`);
}

async function runSearch(options = {}) {
  favoritesViewActive = false;

  if (options.filters) {
    applyFilterMap(options.filters);
  }

  setStatus("Searching cases…");
  searchBtn.disabled = true;
  renderSkeletonCards();

  try {
    const payload = await fetchJson(buildSearchUrl());
    renderResults(payload);
  } catch (error) {
    console.error(error);
    resultsGrid.innerHTML = "";
    resultsCount.textContent = "0 cases";
    setStatus("Unable to load cases. Ensure the backend server is running.", true);
  } finally {
    searchBtn.disabled = false;
  }
}

function handleRegionClick(event) {
  const regionNode = event.target.closest(".anatomy-region");
  if (!regionNode) {
    return;
  }

  const regionId = regionNode.dataset.regionId;
  const regionConfig = findRegionConfig(regionId);

  if (!regionConfig?.filter) {
    return;
  }

  const filters = {
    ...getFilterValues(),
    region: regionConfig.filter.region || "",
    sub_location: regionConfig.filter.sub_location || "",
  };

  runSearch({ filters });
}

function clearFilters() {
  searchInput.value = "";
  filterElements.forEach((select) => {
    select.value = "";
  });
  updateSubLocationOptions();
  updateConditionalFilterVisibility();
  syncExplorerToFilters();
  resultsGrid.innerHTML = "";
  resultsByFolder.clear();
  resultsCount.textContent = "0 cases";
  sortWrap.hidden = true;
  lastPayload = null;
  setStatus("Filters cleared. Click a region on the diagram or search to view cases.");
}

async function loadConfig() {
  try {
    const [filters, anatomy] = await Promise.all([
      fetchJson("/api/filters"),
      fetchJson("/api/anatomy"),
    ]);

    filterConfig = filters;
    anatomyConfig = anatomy;

    populateSelect(filterRegion, filterConfig.regions, "All locations");
    populateSelect(filterFullThickness, excludeNA(filterConfig.full_thicknesses), "Any");
    populateSelect(filterMethod, filterConfig.methods_of_repair, "All methods");
    populateSelect(filterGeneralFlap, excludeNA(filterConfig.general_flaps), "All flap categories");
    populateSelect(
      filterSpecificFlap,
      excludeNA(filterConfig.specific_flap_descriptions),
      "All specific flaps"
    );
    populateSelect(filterGraft, excludeNA(filterConfig.grafts), "All grafts");
    populateSelect(
      filterGraftDonor,
      excludeNA(filterConfig.graft_donor_sites),
      "All donor sites"
    );
    updateSubLocationOptions();
    updateConditionalFilterVisibility();
    syncExplorerToFilters();
  } catch (error) {
    console.error(error);
    if (error.status === 401) {
      setStatus("Sign in to browse cases.", true);
    } else {
      setStatus("Unable to load filters. Ensure the backend server is running.", true);
    }
  }
}


async function initializeApp() {
  await refreshAuthState();
  await loadConfig();
}

searchBtn.addEventListener("click", () => runSearch());
clearBtn.addEventListener("click", clearFilters);
sortSelect.addEventListener("change", () => {
  currentSort = sortSelect.value;
  if (lastPayload) {
    renderCaseList(applySort(lastPayload.cases));
  }
});
resultsGrid.addEventListener("click", (event) => {
  const pearlsToggle = event.target.closest(".case-pearls-toggle");
  if (pearlsToggle) {
    const card = pearlsToggle.closest(".case-card");
    const pearls = pearlsToggle.closest(".case-pearls");
    if (!card || !pearls) {
      return;
    }

    const willOpen = !card.classList.contains("is-pearls-open");

    resultsGrid.querySelectorAll(".case-card.is-pearls-open").forEach((other) => {
      if (other === card) {
        return;
      }
      other.classList.remove("is-pearls-open");
      const otherPearls = other.querySelector(".case-pearls");
      if (otherPearls) {
        otherPearls.style.minHeight = "";
      }
      const otherToggle = other.querySelector(".case-pearls-toggle");
      if (otherToggle) {
        otherToggle.setAttribute("aria-expanded", "false");
        otherToggle.textContent = "More";
      }
    });

    if (willOpen) {
      pearls.style.minHeight = `${pearls.offsetHeight}px`;
      card.classList.add("is-pearls-open");
      pearlsToggle.setAttribute("aria-expanded", "true");
      pearlsToggle.textContent = "Less";
    } else {
      card.classList.remove("is-pearls-open");
      pearls.style.minHeight = "";
      pearlsToggle.setAttribute("aria-expanded", "false");
      pearlsToggle.textContent = "More";
    }
    return;
  }

  const favoriteBtn = event.target.closest(".case-favorite-btn");
  if (favoriteBtn) {
    toggleFavorite(favoriteBtn.dataset.folderName, favoriteBtn);
    return;
  }

  const card = event.target.closest(".case-card");
  if (card) {
    openCaseModal(card.dataset.folderName);
  }
});

resultsGrid.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  if (event.target.closest("button")) {
    return;
  }
  const card = event.target.closest(".case-card");
  if (!card) {
    return;
  }
  event.preventDefault();
  openCaseModal(card.dataset.folderName);
});

caseModalClose.addEventListener("click", closeCaseModal);
caseModal.addEventListener("click", (event) => {
  if (event.target === caseModal) {
    closeCaseModal();
    return;
  }
  const loginBtn = event.target.closest(".comment-login-btn");
  if (loginBtn) {
    openAuthModal("login");
    return;
  }
  const favoriteBtn = event.target.closest(".case-modal-favorite");
  if (favoriteBtn) {
    toggleFavorite(favoriteBtn.dataset.folderName, favoriteBtn);
  }
});
caseModal.addEventListener("submit", (event) => {
  const form = event.target.closest(".comment-form");
  if (!form) {
    return;
  }
  event.preventDefault();
  handleCommentSubmit(form);
});
caseModalDots.addEventListener("click", (event) => {
  const dot = event.target.closest(".carousel-dot");
  if (!dot) {
    return;
  }
  scrollCarouselTo(Number(dot.dataset.index));
});
caseModalPrev.addEventListener("click", () => scrollCarouselBy(-1));
caseModalNext.addEventListener("click", () => scrollCarouselBy(1));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") {
    return;
  }
  if (!caseModal.hidden) {
    closeCaseModal();
  } else if (!authModal.hidden) {
    closeAuthModal();
  }
});

filterRegion.addEventListener("change", () => {
  updateSubLocationOptions();
  updateConditionalFilterVisibility();
  syncExplorerToFilters();
  runSearch();
});
filterSubLocation.addEventListener("change", () => {
  syncExplorerToFilters();
  runSearch();
});
filterMethod.addEventListener("change", () => {
  updateConditionalFilterVisibility();
});
diagramsRoot.addEventListener("click", handleRegionClick);
if (anatomyBackBtn) {
  anatomyBackBtn.addEventListener("click", () => {
    const filters = { ...getFilterValues(), region: "", sub_location: "" };
    runSearch({ filters });
  });
}

searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    runSearch();
  }
});

filterElements.forEach((select) => {
  if (select === filterRegion || select === filterSubLocation) {
    return;
  }
  select.addEventListener("change", () => runSearch());
});

window.addEventListener("resize", () => {
  syncPearlsToggles();
});

authModalClose.addEventListener("click", closeAuthModal);
authModal.addEventListener("click", (event) => {
  if (event.target === authModal) {
    closeAuthModal();
  }
});
authSwitchBtn.addEventListener("click", () => {
  openAuthModal(authMode === "login" ? "register" : "login");
});
authForm.addEventListener("submit", handleAuthSubmit);

initializeApp();
