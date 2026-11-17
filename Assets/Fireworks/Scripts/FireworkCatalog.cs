using System;
using UnityEngine;

namespace Fireworks
{
    /// <summary>The nine fixed firework types of the cue contract (Docs/설계.md).</summary>
    public enum FireworkType
    {
        Peony = 0,
        Chrysanthemum = 1,
        Willow = 2,
        Ring = 3,
        Crossette = 4,
        Strobe = 5,
        Palm = 6,
        Comet = 7,
        Fountain = 8,
    }

    public static class FireworkCatalog
    {
        public const int TypeCount = 9;

        static readonly string[] Names =
        {
            "peony", "chrysanthemum", "willow", "ring", "crossette", "strobe", "palm", "comet", "fountain",
        };

        public static string Id(FireworkType type) => Names[(int)type];

        public static bool TryParse(string id, out FireworkType type)
        {
            type = FireworkType.Peony;
            if (string.IsNullOrEmpty(id)) return false;
            string key = id.Trim().ToLowerInvariant();
            for (int i = 0; i < Names.Length; i++)
            {
                if (Names[i] == key)
                {
                    type = (FireworkType)i;
                    return true;
                }
            }
            return false;
        }

        public static bool IsAerial(FireworkType type) => type != FireworkType.Fountain;

        /// <summary>
        /// Seconds from launch until the shell reaches height <paramref name="h"/> and bursts.
        /// The shell decelerates to zero vertical speed at the apex. Comets climb a little slower.
        /// h=45 → 1.58 s, h=80 → 2.23 s, h=120 → 2.97 s (comet ×1.25).
        /// </summary>
        public static float RiseTime(FireworkType type, float h)
        {
            if (type == FireworkType.Fountain) return 0f;
            float t = 0.75f + 0.0185f * Mathf.Max(0f, h);
            return type == FireworkType.Comet ? t * 1.25f : t;
        }

        /// <summary>Upper bound of how long the effect stays visible after the burst (pool sizing, end-of-show wait).</summary>
        public static float AfterBurstLife(FireworkType type)
        {
            switch (type)
            {
                case FireworkType.Willow: return 7.5f;
                case FireworkType.Strobe: return 4.5f;
                case FireworkType.Palm: return 4f;
                case FireworkType.Comet: return 2.5f;
                default: return 3.5f;
            }
        }

        /// <summary>Smoke lingers after the sparks; pooled objects are only free once it is gone.</summary>
        public const float SmokeLife = 9.5f;
    }

    [Serializable]
    public struct PaletteColors
    {
        public string name;
        [Tooltip("Main star colour (LDR hue, brightness comes from the HDR material intensity)")]
        public Color primary;
        [Tooltip("Colour the stars shift to, or the second colour of a mixed effect")]
        public Color secondary;
        [Tooltip("Pick a random colour from 'mix' per star instead of primary/secondary")]
        public bool mixed;
        public Color[] mix;
    }

    /// <summary>The ten fixed palettes of the cue contract. HDR strength is applied by the particle materials.</summary>
    public static class Palettes
    {
        static readonly string[] Known =
        {
            "gold", "silver", "red", "orange", "green", "blue", "purple", "pink", "cyan", "mixed",
        };

        public static bool IsKnown(string name)
        {
            if (string.IsNullOrEmpty(name)) return false;
            return Array.IndexOf(Known, name.Trim().ToLowerInvariant()) >= 0;
        }

        public static PaletteColors[] Defaults()
        {
            return new[]
            {
                Make("gold", 1.00f, 0.58f, 0.15f, 1.00f, 0.36f, 0.07f),
                Make("silver", 0.86f, 0.90f, 1.00f, 0.66f, 0.76f, 1.00f),
                Make("red", 1.00f, 0.10f, 0.06f, 1.00f, 0.42f, 0.10f),
                Make("orange", 1.00f, 0.38f, 0.04f, 1.00f, 0.70f, 0.20f),
                Make("green", 0.16f, 1.00f, 0.22f, 0.72f, 1.00f, 0.16f),
                Make("blue", 0.10f, 0.34f, 1.00f, 0.42f, 0.22f, 1.00f),
                Make("purple", 0.62f, 0.16f, 1.00f, 1.00f, 0.22f, 0.70f),
                Make("pink", 1.00f, 0.26f, 0.60f, 1.00f, 0.62f, 0.86f),
                Make("cyan", 0.08f, 0.86f, 1.00f, 0.30f, 0.50f, 1.00f),
                new PaletteColors
                {
                    name = "mixed",
                    primary = new Color(1.00f, 0.58f, 0.15f),
                    secondary = Color.white,
                    mixed = true,
                    mix = new[]
                    {
                        new Color(1.00f, 0.10f, 0.06f),
                        new Color(1.00f, 0.58f, 0.15f),
                        new Color(0.16f, 1.00f, 0.22f),
                        new Color(0.10f, 0.34f, 1.00f),
                        new Color(0.62f, 0.16f, 1.00f),
                        new Color(0.08f, 0.86f, 1.00f),
                        new Color(1.00f, 0.26f, 0.60f),
                    },
                },
            };
        }

        static PaletteColors Make(string n, float r, float g, float b, float r2, float g2, float b2)
        {
            return new PaletteColors
            {
                name = n,
                primary = new Color(r, g, b, 1f),
                secondary = new Color(r2, g2, b2, 1f),
                mixed = false,
                mix = new Color[0],
            };
        }
    }
}
