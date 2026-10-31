
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

export function initFurnitureBrowser({
  sceneGroup,
  camera,
  renderer,
  controls,
  floor
}) {
  const panel = document.getElementById("furniture-panel");
  const open = document.getElementById("furniture-open");
  const close = document.getElementById("furniture-close");
  const roomsEl = document.getElementById("furniture-rooms");
  const search = document.getElementById("furniture-search");
  const searchBtn = document.getElementById("furniture-search-button");
  const results = document.getElementById("furniture-results");
  const status = document.getElementById("furniture-status");
  const add = document.getElementById("furniture-add");

  if (!panel || !open || !camera || !renderer || !floor) {
    console.error("Furniture editor: required scene elements are missing.");
    return;
  }

  const canvas = renderer.domElement;
  const loader = new GLTFLoader();
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const floorPlane = new THREE.Plane(
    new THREE.Vector3(0, 1, 0),
    0
  );

  let activeRoom = "living-room";
  let selectedAsset = null;
  let selectedModel = null;
  let pendingModel = null;
  let busy = false;
  let placementMode = false;
  let dragging = false;
  let pointerDownPosition = null;
  let dragOffset = new THREE.Vector3();
  let originalScale = 1;

  const setStatus = message => {
    status.textContent = message;
  };

  // -------------------------------------------------------
  // Furniture editor toolbar
  // -------------------------------------------------------

  const toolbar = document.createElement("div");
  toolbar.id = "furniture-editor-toolbar";

  toolbar.style.cssText = `
    display:none;
    margin-top:14px;
    padding:12px;
    border-radius:12px;
    background:rgba(185,198,255,.09);
    border:1px solid rgba(185,198,255,.22);
    color:#f4f5f8;
  `;

  toolbar.innerHTML = `
    <div style="font-size:12px;font-weight:700;margin-bottom:9px">
      Furniture Editor
    </div>

    <div id="furniture-selected-name"
      style="font-size:11px;color:#c4c9d6;margin-bottom:10px">
      No furniture selected
    </div>

    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">
      <button class="btn" id="furniture-rotate-left" type="button">↶ Rotate</button>
      <button class="btn" id="furniture-rotate-right" type="button">Rotate ↷</button>
      <button class="btn" id="furniture-delete" type="button">Delete</button>
    </div>

    <div style="margin-top:12px">
      <label for="furniture-scale"
        style="font-size:11px;color:#c4c9d6;display:flex;justify-content:space-between">
        <span>Furniture size</span>
        <span id="furniture-scale-value">100%</span>
      </label>
      <input id="furniture-scale" type="range"
        min="25" max="250" step="5" value="100"
        style="width:100%;margin-top:7px;accent-color:#b9c6ff">
    </div>

    <div style="font-size:10px;color:#aeb5c5;margin-top:10px">
      Drag selected furniture on the floor to move it.
    </div>
  `;

  const footer = panel.querySelector(".furniture-footer");
  if (footer) {
    footer.after(toolbar);
  } else {
    panel.append(toolbar);
  }

  const selectedName = toolbar.querySelector("#furniture-selected-name");
  const scaleInput = toolbar.querySelector("#furniture-scale");
  const scaleValue = toolbar.querySelector("#furniture-scale-value");
  const deleteButton = toolbar.querySelector("#furniture-delete");

  function updateToolbar() {
    toolbar.style.display = selectedModel ? "block" : "none";

    if (!selectedModel) return;

    selectedName.textContent =
      selectedModel.userData.furnitureName || "Selected furniture";

    scaleInput.value = Math.round(
      (selectedModel.userData.editorScale || 1) * 100
    );

    scaleValue.textContent = `${scaleInput.value}%`;
  }

  // -------------------------------------------------------
  // Selection highlighting
  // -------------------------------------------------------

  function setSelected(model) {
    if (selectedModel) {
      selectedModel.traverse(node => {
        if (!node.isMesh) return;

        const original = node.userData.originalEmissive;

        if (original && node.material?.emissive) {
          node.material.emissive.copy(original);
        }
      });
    }

    selectedModel = model;

    if (selectedModel) {
      selectedModel.traverse(node => {
        if (!node.isMesh || !node.material) return;

        const materials = Array.isArray(node.material)
          ? node.material
          : [node.material];

        materials.forEach(material => {
          if (!material.emissive) return;

          if (!node.userData.originalEmissive) {
            node.userData.originalEmissive = material.emissive.clone();
          }

          material.emissive.set(0x293b70);
        });
      });
    }

    updateToolbar();
  }

  // -------------------------------------------------------
  // Room categories
  // -------------------------------------------------------

  ROOMS.forEach(([id, label, hint]) => {
    const button = document.createElement("button");

    button.type = "button";
    button.className = "furniture-room";
    button.innerHTML = `<strong>${label}</strong><small>${hint}</small>`;

    button.addEventListener("click", () => {
      activeRoom = id;
      selectedAsset = null;
      add.disabled = true;

      roomsEl.querySelectorAll(".furniture-room")
        .forEach(item => item.classList.toggle("active", item === button));

      search.value = "";
      loadItems();
    });

    roomsEl.append(button);
  });

  roomsEl.firstElementChild?.classList.add("active");

  open.addEventListener("click", () => {
    panel.hidden = false;
    if (!results.dataset.loaded) loadItems();
  });

  close.addEventListener("click", () => {
    panel.hidden = true;
  });

  searchBtn.addEventListener("click", loadItems);

  search.addEventListener("keydown", event => {
    if (event.key === "Enter") loadItems();
  });

  // -------------------------------------------------------
  // Furniture catalogue
  // -------------------------------------------------------

  async function loadItems() {
    results.replaceChildren();
    results.dataset.loaded = "1";

    selectedAsset = null;
    add.disabled = true;

    setStatus("Searching Poly Haven models…");

    try {
      const params = new URLSearchParams({
        room: activeRoom,
        q: search.value.trim(),
        limit: "30"
      });

      const response = await fetch(`/furniture/search?${params}`);

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || response.statusText);
      }

      const data = await response.json();

      (data.results || []).forEach(asset => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "furniture-card";

        const image = document.createElement("img");
        image.loading = "lazy";
        image.alt = asset.name;
        image.src = asset.thumbnail_url || "";

        const name = document.createElement("strong");
        name.textContent = asset.name;

        const category = document.createElement("small");
        category.textContent = asset.category || "3D model";

        card.append(image, name, category);

        card.addEventListener("click", () => {
          selectedAsset = asset;

          results.querySelectorAll(".furniture-card")
            .forEach(item => {
              item.classList.toggle("selected", item === card);
            });

          add.disabled = false;
          setStatus(`${asset.name} selected. Add it to your scene.`);
        });

        results.append(card);
      });

      setStatus(
        data.results?.length
          ? `${data.total} models · Powered by Poly Haven`
          : "No models matched this room. Try another search."
      );

    } catch (error) {
      setStatus(`Furniture catalogue error: ${error.message}`);
    }
  }

  // -------------------------------------------------------
  // Furniture download and loading
  // -------------------------------------------------------

  add.addEventListener("click", async () => {
    if (!selectedAsset || busy) return;

    busy = true;
    add.disabled = true;

    const asset = selectedAsset;

    setStatus(`Downloading ${asset.name}…`);

    try {
      const response = await fetch(
        `/furniture/${encodeURIComponent(asset.id)}/download`,
        { method: "POST" }
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || response.statusText);
      }

      const data = await response.json();

      loader.load(
        data.model_url,

        gltf => {
          const model = gltf.scene;

          model.name = asset.name;
          model.userData.kind = "furniture";
          model.userData.furnitureName = asset.name;
          model.userData.room = activeRoom;

          model.traverse(node => {
            if (node.isMesh) {
              node.castShadow = true;
              node.receiveShadow = true;
            }
          });

          // Normalize the model while preserving proportions.
          const bounds = new THREE.Box3().setFromObject(model);
          const size = bounds.getSize(new THREE.Vector3());
          const maxDimension = Math.max(size.x, size.y, size.z) || 1;

          const initialScale = 0.9 / maxDimension;

          model.scale.setScalar(initialScale);
          model.userData.baseScale = initialScale;
          model.userData.editorScale = 1;

          model.updateMatrixWorld(true);

          const scaledBounds = new THREE.Box3().setFromObject(model);
          const center = scaledBounds.getCenter(new THREE.Vector3());

          model.position.x -= center.x;
          model.position.z -= center.z;
          model.position.y -= scaledBounds.min.y;

          // Wait for the user to click the floor to choose placement.
          model.visible = false;

          sceneGroup.add(model);

          pendingModel = model;
          placementMode = true;

          setSelected(null);

          setStatus(
            `${asset.name} loaded. Click a position on the floor to place it.`
          );

          busy = false;
          add.disabled = false;
        },

        undefined,

        error => {
          setStatus(
            `Could not load model: ${error.message || "GLTF loader error"}`
          );

          busy = false;
          add.disabled = false;
        }
      );

    } catch (error) {
      setStatus(`Could not add furniture: ${error.message}`);

      busy = false;
      add.disabled = false;
    }
  });

  // -------------------------------------------------------
  // Raycasting helpers
  // -------------------------------------------------------

  function updatePointer(event) {
    const rect = canvas.getBoundingClientRect();

    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(pointer, camera);
  }

  function getFurnitureRoot(object) {
    let current = object;

    while (current && current !== sceneGroup) {
      if (current.userData?.kind === "furniture") {
        return current;
      }

      current = current.parent;
    }

    return null;
  }

  function getFloorPoint(event) {
    updatePointer(event);

    const point = new THREE.Vector3();

    const hit = raycaster.ray.intersectPlane(floorPlane, point);

    return hit ? point : null;
  }

  function getFurnitureAtPointer(event) {
    updatePointer(event);

    const furniture = sceneGroup.children.filter(
      object => object.userData?.kind === "furniture" && object.visible
    );

    const hits = raycaster.intersectObjects(furniture, true);

    return hits.length ? getFurnitureRoot(hits[0].object) : null;
  }

  // -------------------------------------------------------
  // Floor placement
  // -------------------------------------------------------

  function placePendingModel(point) {
    if (!pendingModel) return;

    pendingModel.position.set(point.x, 0, point.z);
    pendingModel.visible = true;

    const placed = pendingModel;
    pendingModel = null;
    placementMode = false;

    setSelected(placed);

    setStatus(
      `${placed.userData.furnitureName} placed in the scene. You can now move, resize, rotate or delete it.`
    );
  }

  // -------------------------------------------------------
  // Mouse interaction: selection, placement and movement
  // -------------------------------------------------------

  canvas.addEventListener("pointerdown", event => {
    console.log("Furniture pointerdown detected");

console.log(
  "Furniture roots:",
  sceneGroup.children.filter(
    object => object.userData?.kind === "furniture"
  ).length
);
    if (event.button !== 0) return;

    const hit = getFurnitureAtPointer(event);
    console.log("Furniture roots:", sceneGroup.children.filter(
  object => object.userData?.kind === "furniture"
).length);

    
    console.log("Furniture raycast result:", hit);

    if (hit) {
      setSelected(hit);

      const point = getFloorPoint(event);

      if (point) {
        dragOffset.copy(hit.position).sub(point);
      }

      dragging = true;
      pointerDownPosition = {
        x: event.clientX,
        y: event.clientY
      };

      if (controls) controls.enabled = false;

      event.preventDefault();
      event.stopImmediatePropagation();

      canvas.setPointerCapture?.(event.pointerId);
      return;
    }

    if (placementMode && pendingModel) {
      const point = getFloorPoint(event);

      if (point) {
        placePendingModel(point);
        event.preventDefault();
        event.stopImmediatePropagation();
      }

      return;
    }

    setSelected(null);
  }, true);

  canvas.addEventListener("pointermove", event => {
    if (!dragging || !selectedModel) return;

    const point = getFloorPoint(event);

    if (!point) return;

    selectedModel.position.x = point.x + dragOffset.x;
    selectedModel.position.z = point.z + dragOffset.z;
    selectedModel.position.y = 0;

    event.preventDefault();
  });

  function stopDragging() {
    if (!dragging) return;

    dragging = false;

    if (controls) controls.enabled = true;

    pointerDownPosition = null;
  }

  canvas.addEventListener("pointerup", stopDragging);
  canvas.addEventListener("pointercancel", stopDragging);

  // -------------------------------------------------------
  // Rotation
  // -------------------------------------------------------

  toolbar.querySelector("#furniture-rotate-left")
    .addEventListener("click", () => {
      if (!selectedModel) return;

      selectedModel.rotation.y += THREE.MathUtils.degToRad(15);
    });

  toolbar.querySelector("#furniture-rotate-right")
    .addEventListener("click", () => {
      if (!selectedModel) return;

      selectedModel.rotation.y -= THREE.MathUtils.degToRad(15);
    });

  // -------------------------------------------------------
  // Proportional resizing
  // -------------------------------------------------------

  scaleInput.addEventListener("input", () => {
    if (!selectedModel) return;

    const value = Number(scaleInput.value) / 100;
    const base = selectedModel.userData.baseScale || 1;

    selectedModel.scale.setScalar(base * value);
    selectedModel.userData.editorScale = value;

    scaleValue.textContent = `${Math.round(value * 100)}%`;

    // Keep the model sitting on the floor after scaling.
    const bounds = new THREE.Box3().setFromObject(selectedModel);

    selectedModel.position.y -= bounds.min.y;
  });

  // -------------------------------------------------------
  // Delete selected furniture
  // -------------------------------------------------------

  deleteButton.addEventListener("click", () => {
    if (!selectedModel) return;

    const name = selectedModel.userData.furnitureName;

    sceneGroup.remove(selectedModel);

    selectedModel.traverse(node => {
      if (node.geometry) node.geometry.dispose();

      if (node.material) {
        const materials = Array.isArray(node.material)
          ? node.material
          : [node.material];

        materials.forEach(material => material.dispose());
      }
    });

    selectedModel = null;

    updateToolbar();

    setStatus(`${name} deleted from the scene.`);
  });

  // -------------------------------------------------------
  // Escape cancels pending placement or deselects furniture
  // -------------------------------------------------------

  window.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (pendingModel) {
        sceneGroup.remove(pendingModel);

        pendingModel.traverse(node => {
          if (node.geometry) node.geometry.dispose();
        });

        pendingModel = null;
        placementMode = false;

        setStatus("Furniture placement cancelled.");
      } else {
        setSelected(null);
        setStatus("Furniture deselected.");
      }
    }

    if (
      (event.key === "Delete" || event.key === "Backspace") &&
      selectedModel &&
      !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)
    ) {
      deleteButton.click();
    }
  });
}
