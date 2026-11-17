using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Rendering.Universal.ShaderGUI;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Fireworks.EditorTools
{
    /// <summary>Night sky, harbour water, skyline and the post-processing profile.</summary>
    public static class EnvironmentFactory
    {
        const string EnvDir = FxAssets.Root + "/Environment";
        public const string VolumePath = "Assets/Fireworks/Data/ShowVolume.asset";

        public static Material Sky, Water, City, Hills;
        public static Cubemap Reflection;
        public static Mesh WaterMesh, CityMesh, HillMesh, PierMesh;

        // Same gradient as Fireworks/NightSky (no stars), used for the water's reflection cubemap.
        static readonly Color Zenith = new Color(0.0035f, 0.0045f, 0.013f);
        static readonly Color Horizon = new Color(0.030f, 0.027f, 0.050f);
        static readonly Color GlowCol = new Color(0.085f, 0.050f, 0.050f);
        static readonly Color Ground = new Color(0.002f, 0.0025f, 0.004f);

        public static void Build()
        {
            FxAssets.EnsureDir(EnvDir);

            Sky = LoadOrCreateMat("M_NightSky", Shader.Find("Fireworks/NightSky"));
            Sky.SetColor("_ZenithColor", Zenith);
            Sky.SetColor("_HorizonColor", Horizon);
            Sky.SetColor("_GlowColor", GlowCol);
            Sky.SetColor("_GroundColor", Ground);
            Sky.SetFloat("_GlowHeight", 11f);
            Sky.SetFloat("_StarGrid", 95f);
            Sky.SetFloat("_StarChance", 0.11f);
            Sky.SetFloat("_StarBrightness", 0.5f);
            Sky.SetFloat("_StarPixels", 0.9f);
            Sky.SetFloat("_Twinkle", 0.35f);
            EditorUtility.SetDirty(Sky);

            Reflection = BuildReflection();
            var waterNormal = BuildWaterNormal();
            var windows = BuildWindowTexture();

            var lit = Shader.Find("Universal Render Pipeline/Lit");
            Water = LoadOrCreateMat("M_Water", lit);
            Water.SetColor("_BaseColor", new Color(0.010f, 0.013f, 0.022f));
            Water.SetFloat("_Metallic", 0f);
            Water.SetFloat("_Smoothness", 0.9f);
            Water.SetTexture("_BumpMap", waterNormal);
            Water.SetFloat("_BumpScale", 0.35f);
            Water.SetTextureScale("_BaseMap", new Vector2(70f, 70f));
            Water.SetFloat("_EnvironmentReflections", 1f);
            Water.SetFloat("_SpecularHighlights", 1f);
            BaseShaderGUI.SetMaterialKeywords(Water, LitGUI.SetMaterialKeywords);
            EditorUtility.SetDirty(Water);

            City = LoadOrCreateMat("M_City", lit);
            City.SetColor("_BaseColor", new Color(0.035f, 0.035f, 0.045f));
            City.SetFloat("_Metallic", 0f);
            City.SetFloat("_Smoothness", 0.25f);
            City.SetTexture("_EmissionMap", windows);
            City.SetColor("_EmissionColor", new Color(3.6f, 2.7f, 1.8f)); // bright: windows are a few pixels and mip-averaged with the facade
            // URP re-derives _EMISSION from the GI flags when it validates the material, so the flag must say emissive.
            City.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
            City.SetFloat("_EnvironmentReflections", 0f);
            City.SetFloat("_SpecularHighlights", 0f);
            BaseShaderGUI.SetMaterialKeywords(City, LitGUI.SetMaterialKeywords);
            EditorUtility.SetDirty(City);

            Hills = LoadOrCreateMat("M_Hills", lit);
            Hills.SetColor("_BaseColor", new Color(0.012f, 0.013f, 0.018f));
            Hills.SetFloat("_Smoothness", 0.1f);
            Hills.SetFloat("_EnvironmentReflections", 0f); // grazing-angle sky reflection made pale bars on the embankment
            Hills.SetFloat("_SpecularHighlights", 0f);
            BaseShaderGUI.SetMaterialKeywords(Hills, LitGUI.SetMaterialKeywords);
            EditorUtility.SetDirty(Hills);

            WaterMesh = SaveMesh("SM_Water", BuildWaterMesh());
            CityMesh = SaveMesh("SM_City", BuildCityMesh());
            HillMesh = SaveMesh("SM_Hills", BuildHillMesh());
            PierMesh = SaveMesh("SM_Pier", BuildPierMesh());

            BuildVolumeProfile();
            AssetDatabase.SaveAssets();
        }

        static Material LoadOrCreateMat(string name, Shader shader)
        {
            string path = $"{EnvDir}/{name}.mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(shader);
                AssetDatabase.CreateAsset(m, path);
            }
            else if (m.shader != shader) m.shader = shader;
            return m;
        }

        static Mesh SaveMesh(string name, Mesh mesh)
        {
            string path = $"{EnvDir}/{name}.asset";
            mesh.name = name;
            var existing = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            if (existing != null)
            {
                existing.Clear();
                EditorUtility.CopySerialized(mesh, existing);
                Object.DestroyImmediate(mesh);
                EditorUtility.SetDirty(existing);
                return existing;
            }
            AssetDatabase.CreateAsset(mesh, path);
            return mesh;
        }

        // ------------------------------------------------------------------ sky reflection

        static Color SkyColor(Vector3 d)
        {
            if (d.y >= 0f)
            {
                Color c = Color.Lerp(Horizon, Zenith, Mathf.Pow(d.y, 0.45f));
                float glow = Mathf.Exp(-d.y * 11f) * Mathf.Clamp01(0.35f + 0.65f * d.z);
                return c + GlowCol * glow;
            }
            return Color.Lerp(Horizon * 0.7f, Ground, Mathf.Clamp01(-d.y * 8f));
        }

        static Cubemap BuildReflection()
        {
            const int size = 64;
            string path = $"{EnvDir}/CM_NightReflection.asset";
            var cube = new Cubemap(size, TextureFormat.RGBAHalf, true);
            var faces = new[]
            {
                CubemapFace.PositiveX, CubemapFace.NegativeX, CubemapFace.PositiveY,
                CubemapFace.NegativeY, CubemapFace.PositiveZ, CubemapFace.NegativeZ,
            };
            var px = new Color[size * size];
            foreach (var f in faces)
            {
                for (int y = 0; y < size; y++)
                for (int x = 0; x < size; x++)
                {
                    float u = (x + 0.5f) / size * 2f - 1f;
                    float v = (y + 0.5f) / size * 2f - 1f;
                    Vector3 d = FaceDir(f, u, v).normalized;
                    // Brighten slightly: the water reads the horizon at grazing angles.
                    px[y * size + x] = SkyColor(d) * 1.6f;
                }
                cube.SetPixels(px, f);
            }
            cube.Apply(true);
            var existing = AssetDatabase.LoadAssetAtPath<Cubemap>(path);
            if (existing != null) AssetDatabase.DeleteAsset(path);
            AssetDatabase.CreateAsset(cube, path);
            return cube;
        }

        // Unity cubemap face orientation (y runs top to bottom in each face).
        static Vector3 FaceDir(CubemapFace f, float u, float v)
        {
            switch (f)
            {
                case CubemapFace.PositiveX: return new Vector3(1f, -v, -u);
                case CubemapFace.NegativeX: return new Vector3(-1f, -v, u);
                case CubemapFace.PositiveY: return new Vector3(u, 1f, v);
                case CubemapFace.NegativeY: return new Vector3(u, -1f, -v);
                case CubemapFace.PositiveZ: return new Vector3(u, -v, 1f);
                default: return new Vector3(-u, -v, -1f);
            }
        }

        // ------------------------------------------------------------------ textures

        static Texture2D BuildWaterNormal()
        {
            const int n = 256;
            string path = $"{FxAssets.TexDir}/T_WaterNormal.png";
            var h = new float[n * n];
            var rng = new System.Random(5);
            // Tileable height field: sum of integer-frequency waves, stretched along x (wind ripples).
            var waves = new List<(float kx, float ky, float a, float ph)>();
            for (int i = 0; i < 24; i++)
            {
                int kx = rng.Next(1, 9) * (rng.Next(2) == 0 ? 1 : -1);
                int ky = rng.Next(2, 18);
                float a = 1f / Mathf.Sqrt(kx * kx + ky * ky);
                waves.Add((kx, ky, a, (float)rng.NextDouble() * 6.283f));
            }
            for (int y = 0; y < n; y++)
            for (int x = 0; x < n; x++)
            {
                float s = 0f;
                foreach (var w in waves)
                    s += w.a * Mathf.Sin(6.2831853f * (w.kx * x + w.ky * y) / n + w.ph);
                h[y * n + x] = s;
            }
            var tex = new Texture2D(n, n, TextureFormat.RGBA32, false);
            var px = new Color32[n * n];
            const float strength = 2.2f;
            for (int y = 0; y < n; y++)
            for (int x = 0; x < n; x++)
            {
                float dx = h[y * n + (x + 1) % n] - h[y * n + (x - 1 + n) % n];
                float dy = h[((y + 1) % n) * n + x] - h[((y - 1 + n) % n) * n + x];
                var nrm = new Vector3(-dx * strength, -dy * strength, 1f).normalized;
                px[y * n + x] = new Color(nrm.x * 0.5f + 0.5f, nrm.y * 0.5f + 0.5f, nrm.z * 0.5f + 0.5f, 1f);
            }
            tex.SetPixels32(px);
            tex.Apply();
            File.WriteAllBytes(path, tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            var imp = (TextureImporter)AssetImporter.GetAtPath(path);
            imp.textureType = TextureImporterType.NormalMap;
            imp.wrapMode = TextureWrapMode.Repeat;
            imp.mipmapEnabled = true;
            imp.SaveAndReimport();
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }

        // Window grid: 8 x 8 windows per tile, roughly a third lit with warm/cool variation.
        static Texture2D BuildWindowTexture()
        {
            const int n = 256, cells = 8;
            string path = $"{FxAssets.TexDir}/T_Windows.png";
            var tex = new Texture2D(n, n, TextureFormat.RGBA32, false);
            var px = new Color32[n * n];
            var rng = new System.Random(11);
            var lit = new Color[cells * cells];
            for (int i = 0; i < lit.Length; i++)
            {
                double r = rng.NextDouble();
                if (r < 0.26)
                {
                    float b = 0.35f + 0.65f * (float)rng.NextDouble();
                    lit[i] = rng.NextDouble() < 0.8 ? new Color(1f, 0.78f, 0.5f) * b : new Color(0.7f, 0.85f, 1f) * b;
                }
                else lit[i] = Color.black;
            }
            int cs = n / cells;
            for (int y = 0; y < n; y++)
            for (int x = 0; x < n; x++)
            {
                int cx = x / cs, cy = y / cs;
                int lx = x % cs, ly = y % cs;
                bool inWindow = lx > cs * 0.25f && lx < cs * 0.75f && ly > cs * 0.3f && ly < cs * 0.8f;
                Color c = inWindow ? lit[cy * cells + cx] : Color.black;
                px[y * n + x] = c;
            }
            tex.SetPixels32(px);
            tex.Apply();
            File.WriteAllBytes(path, tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            var imp = (TextureImporter)AssetImporter.GetAtPath(path);
            imp.textureType = TextureImporterType.Default;
            imp.wrapMode = TextureWrapMode.Repeat;
            imp.mipmapEnabled = true;
            imp.filterMode = FilterMode.Bilinear;
            imp.SaveAndReimport();
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }

        // ------------------------------------------------------------------ meshes

        static Mesh BuildWaterMesh()
        {
            // A grid (not one quad) so per-vertex fog/precision stays sane at 4 km.
            const float half = 2000f;
            const int seg = 16;
            var v = new List<Vector3>();
            var uv = new List<Vector2>();
            var nrm = new List<Vector3>();
            var tan = new List<Vector4>();
            var tri = new List<int>();
            for (int y = 0; y <= seg; y++)
            for (int x = 0; x <= seg; x++)
            {
                float fx = x / (float)seg, fy = y / (float)seg;
                v.Add(new Vector3(Mathf.Lerp(-half, half, fx), 0f, Mathf.Lerp(-half, half, fy)));
                uv.Add(new Vector2(fx, fy));
                nrm.Add(Vector3.up);
                tan.Add(new Vector4(1f, 0f, 0f, -1f));
            }
            for (int y = 0; y < seg; y++)
            for (int x = 0; x < seg; x++)
            {
                int i = y * (seg + 1) + x;
                tri.AddRange(new[] { i, i + seg + 1, i + 1, i + 1, i + seg + 1, i + seg + 2 });
            }
            var m = new Mesh();
            m.SetVertices(v);
            m.SetUVs(0, uv);
            m.SetNormals(nrm);
            m.SetTangents(tan);
            m.SetTriangles(tri, 0);
            m.RecalculateBounds();
            return m;
        }

        static void AddBox(List<Vector3> v, List<Vector3> n, List<Vector2> uv, List<int> t, Vector3 c, Vector3 size, float tile)
        {
            Vector3 h = size * 0.5f;
            void Face(Vector3 o, Vector3 right, Vector3 up, Vector3 normal, float w, float hgt)
            {
                int b = v.Count;
                v.Add(o); v.Add(o + right * w); v.Add(o + right * w + up * hgt); v.Add(o + up * hgt);
                for (int i = 0; i < 4; i++) n.Add(normal);
                uv.Add(new Vector2(0, 0)); uv.Add(new Vector2(w / tile, 0)); uv.Add(new Vector2(w / tile, hgt / tile)); uv.Add(new Vector2(0, hgt / tile));
                t.AddRange(new[] { b, b + 2, b + 1, b, b + 3, b + 2 });
            }
            Vector3 min = c - h;
            // front (-z), back (+z), left (-x), right (+x), top
            Face(new Vector3(min.x, min.y, min.z), Vector3.right, Vector3.up, Vector3.back, size.x, size.y);
            Face(new Vector3(min.x + size.x, min.y, min.z + size.z), Vector3.left, Vector3.up, Vector3.forward, size.x, size.y);
            Face(new Vector3(min.x, min.y, min.z + size.z), Vector3.back, Vector3.up, Vector3.left, size.z, size.y);
            Face(new Vector3(min.x + size.x, min.y, min.z), Vector3.forward, Vector3.up, Vector3.right, size.z, size.y);
            int tb = v.Count;
            float yTop = min.y + size.y;
            v.Add(new Vector3(min.x, yTop, min.z)); v.Add(new Vector3(min.x + size.x, yTop, min.z));
            v.Add(new Vector3(min.x + size.x, yTop, min.z + size.z)); v.Add(new Vector3(min.x, yTop, min.z + size.z));
            for (int i = 0; i < 4; i++) { n.Add(Vector3.up); uv.Add(Vector2.zero); }
            t.AddRange(new[] { tb, tb + 3, tb + 2, tb, tb + 2, tb + 1 });
        }

        static Mesh BuildCityMesh()
        {
            var v = new List<Vector3>(); var n = new List<Vector3>(); var uv = new List<Vector2>(); var t = new List<int>();
            var rng = new System.Random(23);
            // Two rows of buildings across the far shore, taller toward the centre-left.
            for (int row = 0; row < 2; row++)
            {
                float z0 = row == 0 ? 290f : 360f;
                float x = -900f;
                while (x < 900f)
                {
                    float w = 14f + (float)rng.NextDouble() * 26f;
                    float d = 12f + (float)rng.NextDouble() * 20f;
                    float centre = Mathf.Exp(-Mathf.Pow((x + 60f) / 260f, 2f));
                    float hgt = (row == 0 ? 10f : 18f) + (float)rng.NextDouble() * (18f + 55f * centre) + (rng.NextDouble() < 0.07 ? 45f : 0f);
                    float z = z0 + (float)rng.NextDouble() * 30f;
                    AddBox(v, n, uv, t, new Vector3(x + w * 0.5f, hgt * 0.5f - 1f, z + d * 0.5f), new Vector3(w, hgt, d), 26f);
                    x += w + 1f + (float)rng.NextDouble() * 6f;
                }
            }
            var m = new Mesh { indexFormat = UnityEngine.Rendering.IndexFormat.UInt32 };
            m.SetVertices(v); m.SetNormals(n); m.SetUVs(0, uv); m.SetTriangles(t, 0);
            m.RecalculateTangents();
            m.RecalculateBounds();
            return m;
        }

        static Mesh BuildPierMesh()
        {
            var v = new List<Vector3>(); var n = new List<Vector3>(); var uv = new List<Vector2>(); var t = new List<int>();
            // Embankment under the skyline.
            AddBox(v, n, uv, t, new Vector3(0f, 1f, 300f), new Vector3(2600f, 4f, 50f), 1000f);
            AddBox(v, n, uv, t, new Vector3(0f, 1.5f, 400f), new Vector3(2800f, 6f, 160f), 1000f);
            var m = new Mesh();
            m.SetVertices(v); m.SetNormals(n); m.SetUVs(0, uv); m.SetTriangles(t, 0);
            m.RecalculateBounds();
            return m;
        }

        static Mesh BuildHillMesh()
        {
            // Two ridge curtains far behind the city; silhouette only.
            var v = new List<Vector3>(); var t = new List<int>(); var n = new List<Vector3>();
            void Ridge(float z, float baseH, float amp, float seed)
            {
                int start = v.Count;
                const int seg = 120;
                for (int i = 0; i <= seg; i++)
                {
                    float x = Mathf.Lerp(-3600f, 3600f, i / (float)seg);
                    float hgt = baseH + amp * (Mathf.PerlinNoise(x * 0.0018f + seed, seed) * 0.8f + Mathf.PerlinNoise(x * 0.006f, seed + 3f) * 0.2f);
                    v.Add(new Vector3(x, -5f, z)); n.Add(Vector3.back);
                    v.Add(new Vector3(x, hgt, z)); n.Add(Vector3.back);
                }
                for (int i = 0; i < seg; i++)
                {
                    int b = start + i * 2;
                    t.AddRange(new[] { b, b + 1, b + 3, b, b + 3, b + 2 });
                }
            }
            Ridge(1100f, 30f, 150f, 1.3f);
            Ridge(1500f, 60f, 230f, 7.9f);
            var m = new Mesh();
            m.SetVertices(v); m.SetNormals(n); m.SetTriangles(t, 0);
            m.RecalculateBounds();
            return m;
        }

        // ------------------------------------------------------------------ post-processing

        static void BuildVolumeProfile()
        {
            var profile = AssetDatabase.LoadAssetAtPath<VolumeProfile>(VolumePath);
            if (profile == null)
            {
                profile = ScriptableObject.CreateInstance<VolumeProfile>();
                AssetDatabase.CreateAsset(profile, VolumePath);
            }
            T Get<T>() where T : VolumeComponent
            {
                if (!profile.TryGet(out T c))
                {
                    c = profile.Add<T>(true);
                    c.name = typeof(T).Name;
                    AssetDatabase.AddObjectToAsset(c, profile);
                }
                c.active = true;
                return c;
            }

            var bloom = Get<Bloom>();
            bloom.threshold.Override(0.95f);
            bloom.intensity.Override(1.1f);
            bloom.scatter.Override(0.72f);
            bloom.highQualityFiltering.Override(true);
            bloom.maxIterations.Override(7);

            var tone = Get<Tonemapping>();
            tone.mode.Override(TonemappingMode.ACES);

            var vig = Get<Vignette>();
            vig.intensity.Override(0.26f);
            vig.smoothness.Override(0.45f);

            var color = Get<ColorAdjustments>();
            color.postExposure.Override(0f);
            color.contrast.Override(6f);
            color.saturation.Override(8f);

            EditorUtility.SetDirty(profile);
        }
    }
}
