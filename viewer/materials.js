
import * as THREE from "three";

export function initMaterialBrowser({ onApply }) {
  const openButton = document.getElementById("materials-open");
  const panel = document.getElementById("materials-panel");
  const closeButton = document.getElementById("materials-close");
  const searchInput = document.getElementById("material-search");
  const searchButton = document.getElementById("material-search-button");
  const resultsEl = document.getElementById("material-results");
  const statusEl = document.getElementById("material-status");
  const resolutionEl = document.getElementById("material-resolution");
  const applyButton = document.getElementById("material-apply");

  // Check that the required HTML elements exist.
  if (
    !openButton ||
    !panel ||
    !closeButton ||
    !searchInput ||
    !searchButton ||
    !resultsEl ||
    !statusEl ||
    !resolutionEl ||
    !applyButton
  ) {
    console.error("Material browser: Required HTML elements are missing.");
    return;
  }

  let selected = null;
  let busy = false;

  function setStatus(message) {
    statusEl.textContent = message;
  }

  // Open material browser.
  openButton.addEventListener("click", () => {
    panel.hidden = false;

    if (!resultsEl.dataset.loaded) {
      searchMaterials("marble");
    }
  });

  // Close material browser.
  closeButton.addEventListener("click", () => {
    panel.hidden = true;
  });

  // Search button.
  searchButton.addEventListener("click", () => {
    searchMaterials(searchInput.value);
  });

  // Search using Enter key.
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      searchMaterials(searchInput.value);
    }
  });

  // Search Poly Haven catalogue.
  async function searchMaterials(query) {
    const q = String(query || "").trim();

    if (!q) {
      setStatus("Enter a material name to search.");
      return;
    }

    resultsEl.replaceChildren();
    resultsEl.dataset.loaded = "1";
    selected = null;
    applyButton.disabled = true;

    setStatus("Searching Poly Haven...");

    try {
      const response = await fetch(
        `/materials/search?q=${encodeURIComponent(q)}&limit=18`
      );

      if (!response.ok) {
        throw new Error(await response.text());
      }

      const data = await response.json();
      const assets = data.results || [];

      if (assets.length === 0) {
        setStatus("No matching materials found. Try another search.");
        return;
      }

      assets.forEach((asset) => {
        const card = document.createElement("button");

        card.type = "button";
        card.className = "material-card";

        const image = document.createElement("img");
        image.loading = "lazy";
        image.alt = asset.name || "Material preview";
        image.src = asset.thumbnail_url || "";

        const name = document.createElement("span");
        name.textContent = asset.name || asset.id;

        card.append(image, name);

        card.addEventListener("click", () => {
          selected = asset;

          resultsEl
            .querySelectorAll(".material-card")
            .forEach((element) => {
              element.classList.remove("selected");
            });

          card.classList.add("selected");

          setStatus(
            `${asset.name} selected. Choose a resolution and apply it to the walls.`
          );

          applyButton.disabled = false;
        });

        resultsEl.appendChild(card);
      });

      setStatus(
        `${data.total ?? assets.length} results · Powered by Poly Haven`
      );

    } catch (error) {
      console.error("Material search failed:", error);
      setStatus(`Catalogue error: ${error.message}`);
    }
  }

  // Load a texture from the backend.
  async function loadTexture(url, isColorMap = false) {
    if (!url) {
      return null;
    }

    const loader = new THREE.TextureLoader();

    const texture = await new Promise((resolve, reject) => {
      loader.load(
        url,
        resolve,
        undefined,
        reject
      );
    });

    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;

    texture.repeat.set(2, 2);

    if (isColorMap) {
      texture.colorSpace = THREE.SRGBColorSpace;
    }

    texture.anisotropy = 8;
    texture.needsUpdate = true;

    return texture;
  }

  // Download and apply selected material.
  applyButton.addEventListener("click", async () => {
    if (!selected || busy) {
      return;
    }

    busy = true;
    applyButton.disabled = true;

    const selectedMaterial = selected;

    setStatus(
      `Preparing ${selectedMaterial.name} textures...`
    );

    try {
      const resolution = resolutionEl.value;

      const response = await fetch(
        `/materials/${encodeURIComponent(selectedMaterial.id)}/download?resolution=${encodeURIComponent(resolution)}`,
        {
          method: "POST"
        }
      );

      if (!response.ok) {
        throw new Error(await response.text());
      }

      const data = await response.json();

      if (!data.maps || !data.maps.color) {
        throw new Error("The downloaded material has no color texture.");
      }

      const maps = data.maps;

      const [
        colorMap,
        normalMap,
        roughnessMap,
        aoMap
      ] = await Promise.all([
        loadTexture(maps.color, true),
        loadTexture(maps.normal),
        loadTexture(maps.roughness),
        loadTexture(maps.ao)
      ]);

      const material = new THREE.MeshStandardMaterial({
        map: colorMap,
        normalMap: normalMap,
        roughnessMap: roughnessMap,
        aoMap: aoMap,
        roughness: 0.85,
        metalness: 0
      });

      if (typeof onApply === "function") {
        onApply(material);
      } else {
        material.dispose();
        throw new Error("Wall material application callback is missing.");
      }

      setStatus(
        `${selectedMaterial.name} applied to walls · ${resolution.toUpperCase()}`
      );

    } catch (error) {
      console.error("Material application failed:", error);
      setStatus(`Could not apply material: ${error.message}`);

    } finally {
      busy = false;
      applyButton.disabled = !selected;
    }
  });
}
