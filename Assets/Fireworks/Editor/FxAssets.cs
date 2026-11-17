using System.IO;
using UnityEditor;
using UnityEditor.Rendering.Universal.ShaderGUI;
using UnityEngine;

namespace Fireworks.EditorTools
{
    /// <summary>Generated textures and materials shared by the firework prefabs.</summary>
    public static class FxAssets
    {
        public const string Root = "Assets/Fireworks/Generated";
        public const string TexDir = Root + "/Textures";
        public const string MatDir = Root + "/Materials";

        public static Material Star, StarHot, Spark, Glow, Trail, Strobe, Glitter, Smoke;

        public static void Build()
        {
            EnsureDir(TexDir);
            EnsureDir(MatDir);

            var dot = SaveTexture("T_SoftDot", 128, 128, SoftDot, false);
            var glow = SaveTexture("T_Glow", 128, 128, GlowFalloff, false);
            var trail = SaveTexture("T_Trail", 64, 32, TrailProfile, false);
            var strobe = SaveTexture("T_StrobeSheet", 128, 64, StrobeSheet, false);
            var smoke = SaveTexture("T_Smoke", 128, 128, SmokePuff, false);

            // HDR strength lives in _BaseColor: particle vertex colours are 8-bit, so they only carry the hue.
            Star = Additive("M_Star", dot, 4.5f);
            StarHot = Additive("M_StarHot", dot, 10f);
            Spark = Additive("M_Spark", dot, 3.2f);
            Glow = Additive("M_Glow", glow, 1.4f);
            Trail = Additive("M_Trail", trail, 3.0f);
            Strobe = Additive("M_Strobe", strobe, 7f);
            Glitter = Additive("M_Glitter", strobe, 4f);
            Smoke = AlphaBlended("M_Smoke", smoke);
            AssetDatabase.SaveAssets();
        }

        public static void EnsureDir(string assetPath)
        {
            if (!Directory.Exists(assetPath))
            {
                Directory.CreateDirectory(assetPath);
                AssetDatabase.Refresh();
            }
        }

        // ------------------------------------------------------------------ textures

        delegate float AlphaFn(float u, float v);

        static Texture2D SaveTexture(string name, int w, int h, AlphaFn fn, bool mips)
        {
            var tex = new Texture2D(w, h, TextureFormat.RGBA32, false);
            var px = new Color32[w * h];
            for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
            {
                float a = Mathf.Clamp01(fn((x + 0.5f) / w, (y + 0.5f) / h));
                px[y * w + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255f));
            }
            tex.SetPixels32(px);
            tex.Apply();
            string path = $"{TexDir}/{name}.png";
            File.WriteAllBytes(path, tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            var imp = (TextureImporter)AssetImporter.GetAtPath(path);
            imp.textureType = TextureImporterType.Default;
            imp.sRGBTexture = false;
            imp.alphaSource = TextureImporterAlphaSource.FromInput;
            imp.alphaIsTransparency = false;
            imp.mipmapEnabled = true;
            imp.wrapMode = TextureWrapMode.Clamp;
            imp.filterMode = FilterMode.Bilinear;
            imp.textureCompression = TextureImporterCompression.Uncompressed;
            imp.npotScale = TextureImporterNPOTScale.None;
            imp.SaveAndReimport();
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }

        static float Radius(float u, float v)
        {
            float dx = u * 2f - 1f, dy = v * 2f - 1f;
            return Mathf.Sqrt(dx * dx + dy * dy);
        }

        // Hot core plus soft halo, exactly zero at the edge.
        static float SoftDot(float u, float v)
        {
            float r = Radius(u, v);
            float edge = Mathf.Clamp01(1f - r);
            float core = Mathf.Exp(-r * r / 0.018f);
            float halo = Mathf.Exp(-r * r / 0.12f) * 0.45f;
            return (core + halo) * Mathf.SmoothStep(0f, 1f, edge * 3f);
        }

        static float GlowFalloff(float u, float v)
        {
            float r = Mathf.Clamp01(Radius(u, v));
            return Mathf.Pow(1f - r, 2.6f);
        }

        // Across-width profile for trails and ribbons (v), flat along the length (u).
        static float TrailProfile(float u, float v)
        {
            float a = Mathf.Sin(v * Mathf.PI);
            return Mathf.Pow(a, 1.6f);
        }

        // Two cells: lit star / dark. Animated with the texture sheet module to strobe.
        static float StrobeSheet(float u, float v)
        {
            if (u >= 0.5f) return 0f;
            float r = Radius(u * 2f, v);
            float core = Mathf.Exp(-r * r / 0.03f);
            float halo = Mathf.Exp(-r * r / 0.16f) * 0.3f;
            return (core + halo) * Mathf.Clamp01((1f - r) * 3f);
        }

        static float SmokePuff(float u, float v)
        {
            float r = Radius(u, v);
            float fall = Mathf.Clamp01(1f - r);
            fall = fall * fall * (3f - 2f * fall);
            float n = 0f, amp = 0.55f, freq = 3f;
            for (int o = 0; o < 4; o++)
            {
                n += amp * Mathf.PerlinNoise(u * freq + 13.1f * o, v * freq + 7.7f * o);
                amp *= 0.5f;
                freq *= 2.1f;
            }
            return Mathf.Clamp01(fall * (n * 1.4f - 0.15f));
        }

        // ------------------------------------------------------------------ materials

        static Material LoadOrCreate(string name, Shader shader)
        {
            string path = $"{MatDir}/{name}.mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(shader);
                AssetDatabase.CreateAsset(m, path);
            }
            else if (m.shader != shader)
            {
                m.shader = shader;
            }
            m.shaderKeywords = null;
            return m;
        }

        static Shader ParticleUnlit => Shader.Find("Universal Render Pipeline/Particles/Unlit");

        static Material Additive(string name, Texture2D tex, float intensity)
        {
            var m = LoadOrCreate(name, ParticleUnlit);
            m.SetTexture("_BaseMap", tex);
            m.SetColor("_BaseColor", new Color(intensity, intensity, intensity, 1f));
            m.SetFloat("_Surface", 1f); // transparent
            m.SetFloat("_Blend", 2f);   // additive
            m.SetFloat("_ColorMode", 0f);
            m.SetFloat("_SoftParticlesEnabled", 0f);
            BaseShaderGUI.SetMaterialKeywords(m, null, ParticleGUI.SetMaterialKeywords);
            EditorUtility.SetDirty(m);
            return m;
        }

        static Material AlphaBlended(string name, Texture2D tex)
        {
            var m = LoadOrCreate(name, ParticleUnlit);
            m.SetTexture("_BaseMap", tex);
            m.SetColor("_BaseColor", Color.white);
            m.SetFloat("_Surface", 1f);
            m.SetFloat("_Blend", 0f); // alpha
            m.SetFloat("_ColorMode", 0f);
            BaseShaderGUI.SetMaterialKeywords(m, null, ParticleGUI.SetMaterialKeywords);
            EditorUtility.SetDirty(m);
            return m;
        }
    }
}
