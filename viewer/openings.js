import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

export function initOpeningsReplacer({
  sceneGroup,
  doorHeight = 0.40,
  windowSill = 0.16,
  windowTop = 0.45,
}) {
  const loader = new GLTFLoader();

  const assetPromises = {
    door: null,
    window: null,
  };

  async function getAsset(kind) {
    if (!assetPromises[kind]) {
      assetPromises[kind] = (async () => {
        const search = await fetch(
          `/openings/search?kind=${kind}&limit=12`
        );

        if (!search.ok) {
          throw new Error(`Could not search ${kind} models`);
        }

        const data = await search.json();
        const candidates = data.results || [];

        if (!candidates.length) {
          throw new Error(`No suitable ${kind} GLTF assets found`);
        }

        // Try candidates in catalogue order until one downloads.
        for (const candidate of candidates) {
          try {
            const response = await fetch(
              `/openings/${encodeURIComponent(candidate.id)}/download`,
              { method: "POST" }
            );

            if (!response.ok) continue;

            const result = await response.json();

            return {
              ...candidate,
              modelUrl: result.model_url,
            };
          } catch (error) {
            console.warn("Opening asset unavailable:", candidate.id, error);
          }
        }

        throw new Error(`No downloadable ${kind} GLTF assets`);
      })();
    }

    return assetPromises[kind];
  }

  function polygonBounds(polygon, scale, cx, cy) {
    const points = polygon.outer || [];

    if (!points.length) {
      throw new Error("Opening polygon has no points");
    }

    const xs = points.map(([x]) => (x - cx) * scale);
    const zs = points.map(([, y]) => -(y - cy) * scale);

    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minZ = Math.min(...zs);
    const maxZ = Math.max(...zs);

    return {
      centerX: (minX + maxX) / 2,
      centerZ: (minZ + maxZ) / 2,
      widthX: maxX - minX,
      widthZ: maxZ - minZ,
    };
  }

  function fitModel(root, target, kind) {
    root.updateMatrixWorld(true);

    let box = new THREE.Box3().setFromObject(root);
    let size = box.getSize(new THREE.Vector3());

    if (
      size.x < 0.0001 ||
      size.y < 0.0001 ||
      size.z < 0.0001
    ) {
      throw new Error("Invalid model dimensions");
    }

    // Align the model's longest horizontal axis with the detected opening.
    const sourceAlongX = size.x >= size.z;
    const targetAlongX = target.widthX >= target.widthZ;

    if (sourceAlongX !== targetAlongX) {
      root.rotation.y += Math.PI / 2;
    }

    root.updateMatrixWorld(true);
    box = new THREE.Box3().setFromObject(root);
    size = box.getSize(new THREE.Vector3());

    const targetWidth = Math.max(
      target.widthX,
      target.widthZ,
      0.05
    );

    const targetHeight =
      kind === "door"
        ? doorHeight
        : windowTop - windowSill;

    const horizontalScale =
      targetWidth / Math.max(size.x, size.z);

    const verticalScale = targetHeight / size.y;

    root.scale.set(
      root.scale.x * horizontalScale,
      root.scale.y * verticalScale,
      root.scale.z * horizontalScale
    );

    root.updateMatrixWorld(true);
    box = new THREE.Box3().setFromObject(root);

    // Center on the detected opening and place its base at the right height.
    root.position.x += target.centerX - (box.min.x + box.max.x) / 2;
    root.position.z += target.centerZ - (box.min.z + box.max.z) / 2;

    const baseY = kind === "door" ? 0 : windowSill;
    root.position.y += baseY - box.min.y;

    root.updateMatrixWorld(true);
  }

  async function replacePlaceholder({
    placeholder,
    polygon,
    scale,
    cx,
    cy,
    kind,
  }) {
    if (!placeholder || !["door", "window"].includes(kind)) {
      return false;
    }

    try {
      const asset = await getAsset(kind);

      const gltf = await new Promise((resolve, reject) => {
        loader.load(asset.modelUrl, resolve, undefined, reject);
      });

      const root = gltf.scene;

      const target = polygonBounds(polygon, scale, cx, cy);
      fitModel(root, target, kind);

      root.traverse(object => {
        if (object.isMesh) {
          object.castShadow = true;
          object.receiveShadow = true;
        }
      });

      root.userData.kind = kind;
      root.userData.isOpeningAsset = true;
      root.userData.assetId = asset.id;
      root.userData.assetName = asset.name;

      sceneGroup.add(root);

      // Only hide the placeholder once the replacement is safely in the scene.
      placeholder.visible = false;
      placeholder.userData.replacedBy = root;

      return true;
    } catch (error) {
      console.warn(`Could not replace detected ${kind}:`, error);
      // Keep the original placeholder visible if anything fails.
      return false;
    }
  }

  return {
    replacePlaceholder,
  };
}
