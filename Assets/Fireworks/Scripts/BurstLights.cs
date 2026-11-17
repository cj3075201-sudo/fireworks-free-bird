using UnityEngine;

namespace Fireworks
{
    /// <summary>
    /// Small pool of point lights that flash at each burst so water and city pick up the colour.
    /// Intensity is a pure function of song time, so edit-mode captures match play mode.
    /// </summary>
    public class BurstLights : MonoBehaviour
    {
        [Range(1, 32)] public int poolSize = 12;
        [Tooltip("Peak intensity for a scale-1 burst")]
        public float peakIntensity = 9000f;
        [Tooltip("Light range, metres")]
        public float range = 420f;
        [Tooltip("Decay time constant, seconds")]
        public float decay = 0.35f;

        struct Slot
        {
            public Light light;
            public double start;
            public float peak;
        }

        Slot[] _slots;

        void Awake() => EnsurePool();

        void EnsurePool()
        {
            if (_slots != null && _slots.Length == poolSize && _slots[0].light != null) return;
            for (int i = transform.childCount - 1; i >= 0; i--)
            {
                var c = transform.GetChild(i).gameObject;
                if (Application.isPlaying) Destroy(c); else DestroyImmediate(c);
            }
            _slots = new Slot[poolSize];
            for (int i = 0; i < poolSize; i++)
            {
                var go = new GameObject($"Burst Light {i}");
                go.transform.SetParent(transform, false);
                var l = go.AddComponent<Light>();
                l.type = LightType.Point;
                l.range = range;
                l.shadows = LightShadows.None;
                l.intensity = 0f;
                l.enabled = false;
                _slots[i] = new Slot { light = l, start = -999, peak = 0f };
            }
        }

        public void Flash(Vector3 position, Color color, float strength, double songTime)
        {
            EnsurePool();
            int pick = 0;
            double oldest = double.MaxValue;
            for (int i = 0; i < _slots.Length; i++)
            {
                if (_slots[i].start < oldest) { oldest = _slots[i].start; pick = i; }
            }
            var s = _slots[pick];
            s.start = songTime;
            s.peak = peakIntensity * strength;
            s.light.transform.position = position;
            s.light.color = color;
            s.light.range = range;
            _slots[pick] = s;
        }

        public void Evaluate(double songTime)
        {
            if (_slots == null) return;
            for (int i = 0; i < _slots.Length; i++)
            {
                var s = _slots[i];
                float age = (float)(songTime - s.start);
                float k = age < 0f ? 0f : s.peak * Mathf.Exp(-age / decay);
                bool on = k > 1f;
                s.light.enabled = on;
                s.light.intensity = on ? k : 0f;
            }
        }

        public void Clear()
        {
            if (_slots == null) return;
            for (int i = 0; i < _slots.Length; i++)
            {
                _slots[i].start = -999;
                _slots[i].peak = 0f;
                _slots[i].light.enabled = false;
            }
        }
    }
}
