using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Fireworks
{
    /// <summary>
    /// Camera director. The camera pose is a pure function of song time: each cue-sheet section gets a shot
    /// (slow move from A to B) and section boundaries blend over <see cref="blendTime"/> seconds.
    /// Hit cues add a short exposure flash and a small shake. Bloom follows the section energy.
    /// </summary>
    public class ShowCamera : MonoBehaviour
    {
        [System.Serializable]
        public struct Shot
        {
            public string label;
            public Vector3 posA, lookA, posB, lookB;
            [Tooltip("Vertical field of view, degrees")] public float fov;
        }

        public Camera cam;
        public Volume volume;

        [Tooltip("Seconds to glide from one section's shot to the next")]
        public float blendTime = 3.5f;
        [Tooltip("Camera shake on hit cues, degrees")]
        public float shakeDegrees = 0.35f;
        [Tooltip("Shake and flash decay time constant, seconds")]
        public float hitDecay = 0.22f;
        [Tooltip("Exposure added on a hit cue, EV")]
        public float flashExposure = 0.45f;
        [Tooltip("Bloom intensity at energy 0 and energy 1")]
        public Vector2 bloomRange = new Vector2(0.9f, 1.5f);

        [Tooltip("Set by the free camera while the user flies around; the director then stops moving the camera")]
        public bool userControl;

        ShowSection[] _sections = new ShowSection[0];
        Shot[] _shots = new Shot[0];
        float _endTime = 60f;
        double _hitTime = -999;
        float _hitStrength;
        Bloom _bloom;
        ColorAdjustments _color;

        public static Shot DefaultShot => MakeShot("wide", new Vector3(0, 10, -205), new Vector3(0, 62, 10), new Vector3(0, 12, -190), new Vector3(0, 66, 10), 52f);

        static Shot MakeShot(string label, Vector3 pa, Vector3 la, Vector3 pb, Vector3 lb, float fov)
        {
            return new Shot { label = label, posA = pa, lookA = la, posB = pb, lookB = lb, fov = fov };
        }

        /// <summary>Shot variants per section name; repeated sections cycle through them.</summary>
        static List<Shot> Variants(string name)
        {
            var v = new List<Shot>();
            switch (name)
            {
                case "intro":
                    v.Add(MakeShot("intro low", new Vector3(0, 5, -240), new Vector3(0, 52, 10), new Vector3(0, 8, -205), new Vector3(0, 60, 10), 50f));
                    break;
                case "verse":
                    v.Add(MakeShot("verse left", new Vector3(-75, 9, -175), new Vector3(10, 62, 10), new Vector3(-60, 11, -168), new Vector3(5, 64, 10), 52f));
                    v.Add(MakeShot("verse right", new Vector3(75, 9, -175), new Vector3(-10, 62, 10), new Vector3(60, 11, -168), new Vector3(-5, 64, 10), 52f));
                    break;
                case "build":
                    v.Add(MakeShot("build crane", new Vector3(18, 4, -160), new Vector3(0, 56, 10), new Vector3(8, 30, -180), new Vector3(0, 74, 10), 54f));
                    v.Add(MakeShot("build crane L", new Vector3(-18, 4, -160), new Vector3(0, 56, 10), new Vector3(-8, 28, -180), new Vector3(0, 72, 10), 54f));
                    break;
                case "chorus":
                    v.Add(MakeShot("chorus centre", new Vector3(0, 14, -165), new Vector3(0, 70, 10), new Vector3(0, 12, -150), new Vector3(0, 72, 10), 56f));
                    v.Add(MakeShot("chorus high right", new Vector3(50, 24, -170), new Vector3(0, 70, 10), new Vector3(38, 26, -162), new Vector3(0, 72, 10), 56f));
                    break;
                case "bridge":
                    v.Add(MakeShot("bridge side", new Vector3(95, 4, -125), new Vector3(-15, 52, 10), new Vector3(82, 5, -118), new Vector3(-10, 56, 10), 54f));
                    break;
                case "finale":
                    v.Add(MakeShot("finale wide", new Vector3(0, 10, -195), new Vector3(0, 70, 10), new Vector3(0, 14, -225), new Vector3(0, 74, 10), 58f));
                    break;
                case "outro":
                    v.Add(MakeShot("outro drift", new Vector3(0, 10, -215), new Vector3(0, 60, 10), new Vector3(-14, 6, -240), new Vector3(0, 48, 10), 52f));
                    break;
            }
            if (v.Count == 0) v.Add(DefaultShot);
            return v;
        }

        void Awake()
        {
            if (cam == null) cam = GetComponent<Camera>();
            FetchOverrides();
        }

        void FetchOverrides()
        {
            if (volume == null || volume.sharedProfile == null) return;
            // Runtime: instance profile so the asset on disk is not modified. Edit mode: use the shared one.
            var profile = Application.isPlaying ? volume.profile : volume.sharedProfile;
            profile.TryGet(out _bloom);
            profile.TryGet(out _color);
        }

        public void Setup(ShowSection[] sections, float endTime)
        {
            _sections = sections ?? new ShowSection[0];
            _endTime = Mathf.Max(1f, endTime);
            var used = new Dictionary<string, int>();
            _shots = new Shot[_sections.Length];
            for (int i = 0; i < _sections.Length; i++)
            {
                string n = _sections[i].name;
                used.TryGetValue(n, out int k);
                var vars = Variants(n);
                _shots[i] = vars[k % vars.Count];
                used[n] = k + 1;
            }
            _hitTime = -999;
            _hitStrength = 0f;
            if (_bloom == null) FetchOverrides();
        }

        public void Hit(double songTime, float strength)
        {
            // Overlapping hits keep the stronger remaining value.
            float remaining = _hitStrength * Mathf.Exp(-(float)(songTime - _hitTime) / hitDecay);
            _hitTime = songTime;
            _hitStrength = Mathf.Max(strength, remaining);
        }

        public float EnergyAt(double t)
        {
            if (_sections.Length == 0) return 0.5f;
            int i = SectionIndex(t);
            float e = _sections[i].energy;
            if (i > 0 && t - _sections[i].start < blendTime)
            {
                float w = Smooth((float)((t - _sections[i].start) / blendTime));
                e = Mathf.Lerp(_sections[i - 1].energy, e, w);
            }
            return e;
        }

        int SectionIndex(double t)
        {
            int idx = 0;
            for (int i = 0; i < _sections.Length; i++)
                if (t >= _sections[i].start) idx = i;
            return idx;
        }

        static float Smooth(float u)
        {
            u = Mathf.Clamp01(u);
            return u * u * (3f - 2f * u);
        }

        void Pose(int i, double t, out Vector3 pos, out Vector3 look, out float fov)
        {
            Shot s;
            float u;
            if (_sections.Length == 0)
            {
                s = DefaultShot;
                u = (float)(t / _endTime);
            }
            else
            {
                s = _shots[i];
                var sec = _sections[i];
                float end = i == _sections.Length - 1 ? Mathf.Max(sec.end, _endTime) : sec.end;
                u = (float)((t - sec.start) / Mathf.Max(0.01f, end - sec.start));
            }
            float w = Smooth(u);
            pos = Vector3.Lerp(s.posA, s.posB, w);
            look = Vector3.Lerp(s.lookA, s.lookB, w);
            fov = s.fov;
        }

        /// <summary>Place the camera for song time <paramref name="t"/> (may be negative during the title).</summary>
        public void Evaluate(double t)
        {
            if (cam == null) return;
            int i = SectionIndex(t);
            Pose(i, t, out var pos, out var look, out float fov);
            if (_sections.Length > 0 && i > 0 && t - _sections[i].start < blendTime)
            {
                Pose(i - 1, t, out var p0, out var l0, out float f0);
                float w = Smooth((float)((t - _sections[i].start) / blendTime));
                pos = Vector3.Lerp(p0, pos, w);
                look = Vector3.Lerp(l0, look, w);
                fov = Mathf.Lerp(f0, fov, w);
            }

            float hit = _hitStrength * Mathf.Exp(-Mathf.Max(0f, (float)(t - _hitTime)) / hitDecay);
            if (t < _hitTime) hit = 0f;

            if (!userControl)
            {
                var rot = Quaternion.LookRotation(look - pos, Vector3.up);
                if (hit > 0.001f)
                {
                    float n = (float)t * 31f;
                    var shake = new Vector3(Mathf.PerlinNoise(n, 0.3f) - 0.5f, Mathf.PerlinNoise(0.7f, n) - 0.5f, 0f) * (2f * shakeDegrees * hit);
                    rot *= Quaternion.Euler(shake);
                }
                transform.SetPositionAndRotation(pos, rot);
                cam.fieldOfView = fov;
            }

            if (_color != null) _color.postExposure.value = flashExposure * hit;
            if (_bloom != null) _bloom.intensity.value = Mathf.Lerp(bloomRange.x, bloomRange.y, EnergyAt(t));
        }
    }
}
