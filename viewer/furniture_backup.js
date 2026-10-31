import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const ROOMS = [
  ["living-room", "Living Room", "Sofa · Tables · Cabinets"],
  ["bedroom", "Bedroom", "Beds · Wardrobes · Dressers"],
  ["kitchen", "Kitchen", "Cabinets · Counters · Appliances"],
  ["bathroom", "Bathroom", "Vanities · Sinks · Fixtures"],
  ["dining-room", "Dining Room", "Tables · Chairs · Sideboards"],
  ["office", "Office", "Desks · Chairs · Shelves"],
  ["outdoor", "Balcony / Outdoor", "Patio · Garden · Seating"],
];

export function initFurnitureBrowser({ sceneGroup }) {
  const panel = document.getElementById("furniture-panel");
  const open = document.getElementById("furniture-open");
  const close = document.getElementById("furniture-close");
  const roomsEl = document.getElementById("furniture-rooms");
  const search = document.getElementById("furniture-search");
  const searchBtn = document.getElementById("furniture-search-button");
  const results = document.getElementById("furniture-results");
  const status = document.getElementById("furniture-status");
  const add = document.getElementById("furniture-add");
  if (!panel || !open) return;

  let activeRoom = "living-room", selected = null, busy = false;
  const loader = new GLTFLoader();
  const setStatus = value => { status.textContent = value; };

  ROOMS.forEach(([id, label, hint]) => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "furniture-room";
    button.innerHTML = `<strong>${label}</strong><small>${hint}</small>`;
    button.addEventListener("click", () => {
      activeRoom = id; selected = null; add.disabled = true;
      roomsEl.querySelectorAll(".furniture-room").forEach(x => x.classList.toggle("active", x === button));
      search.value = ""; loadItems();
    });
    roomsEl.append(button);
  });
  roomsEl.firstElementChild?.classList.add("active");
  open.addEventListener("click", () => { panel.hidden = false; if (!results.dataset.loaded) loadItems(); });
  close.addEventListener("click", () => { panel.hidden = true; });
  searchBtn.addEventListener("click", loadItems);
  search.addEventListener("keydown", e => { if (e.key === "Enter") loadItems(); });

  async function loadItems() {
    results.replaceChildren(); results.dataset.loaded = "1"; selected = null; add.disabled = true;
    setStatus("Searching Poly Haven models…");
    try {
      const params = new URLSearchParams({ room: activeRoom, q: search.value.trim(), limit: "30" });
      const response = await fetch(`/furniture/search?${params}`);
      if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
      const data = await response.json();
      (data.results || []).forEach(asset => {
        const card = document.createElement("button"); card.type = "button"; card.className = "furniture-card";
        const img = document.createElement("img"); img.loading = "lazy"; img.alt = asset.name; img.src = asset.thumbnail_url || "";
        const name = document.createElement("strong"); name.textContent = asset.name;
        const category = document.createElement("small"); category.textContent = asset.category || "3D model";
        card.append(img, name, category);
        card.addEventListener("click", () => {
          selected = asset; results.querySelectorAll(".furniture-card").forEach(x => x.classList.toggle("selected", x === card));
          add.disabled = false; setStatus(`${asset.name} selected. Add it to your 3D scene.`);
        });
        results.append(card);
      });
      setStatus(data.results?.length ? `${data.total} models · Powered by Poly Haven` : "No models matched this room. Try a broader search or another room.");
    } catch (error) { setStatus(`Furniture catalogue error: ${error.message}`); }
  }

  add.addEventListener("click", async () => {
    if (!selected || busy) return;
    busy = true; add.disabled = true; setStatus(`Loading ${selected.name}…`);
    try {
      const response = await fetch(`/furniture/${encodeURIComponent(selected.id)}/download`, { method: "POST" });
      if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
      const data = await response.json();
      loader.load(data.model_url, gltf => {
        const model = gltf.scene;
        model.name = selected.name;
        model.userData.kind = "furniture";
        model.traverse(node => {
          if (node.isMesh) { node.castShadow = true; node.receiveShadow = true; }
        });
        const bounds = new THREE.Box3().setFromObject(model);
        const size = bounds.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z) || 1;
        model.scale.setScalar(0.9 / maxDim);
        model.updateMatrixWorld(true);
        const scaledBounds = new THREE.Box3().setFromObject(model);
        const center = scaledBounds.getCenter(new THREE.Vector3());
        model.position.x -= center.x; model.position.z -= center.z; model.position.y -= scaledBounds.min.y;
        sceneGroup.add(model);
        setStatus(`${selected.name} added to the scene. Use the camera controls to inspect it.`);
        busy = false; add.disabled = !selected;
      }, undefined, error => {
        setStatus(`Could not load model: ${error.message || "GLB/glTF loader error"}`); busy = false; add.disabled = !selected;
      });
    } catch (error) { setStatus(`Could not add furniture: ${error.message}`); busy = false; add.disabled = !selected; }
  });
}
