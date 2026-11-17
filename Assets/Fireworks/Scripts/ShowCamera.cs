using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Fireworks
{
    /// <summary>
    /// Camera director. The camera pose is a pure function of song time, built from segments:
    /// without a drop, one segment per cue-sheet section (slow move A→B, boundaries blend over <see cref="blendTime"/>).
    /// With a drop (slow-burn songs): one very slow push-in until the drop, a fast whip to a low wide shot tilted to
    /// the sky at the drop, then the section shots again. Hit cues and the drop add an exposure flash and a shake.
    /// Bloom follows section energy and is held down before the drop so the drop reads bigger.
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

        struct Segment
        {
            public double start, end;
            public Shot shot;
            public float blendIn;   // seconds to blend in from the previous segment
            public bool whip;       // ease-out blend (fast start) instead of smoothstep
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
        [Tooltip("Upper limit of the flash exposure so a big moment never burns the frame white, EV")]
        public float maxFlashExposure = 0.85f;
        [Tooltip("Bloom intensity at energy 0 and energy 1")]
        public Vector2 bloomRange = new Vector2(0.9f, 1.5f);

        [Header("Drop (slow-burn songs)")]
        [Tooltip("Hit strength of the drop; a scale-1.6 hit cue is 1.6")]
        public float dropStrength = 2.4f;
        [Tooltip("Decay of the drop flash and shake, seconds")]
        public float dropDecay = 0.4f;
        [Tooltip("Seconds of the fast camera whip into the drop shot")]
        public float dropWhip = 0.35f;
        [Tooltip("Seconds the drop shot holds before section shots resume")]
        public float dropHold = 7f;
        [Tooltip("Exposure before the drop, EV (quiet build-up)")]
        public float preDropExposure = -0.3f;
        [Tooltip("Bloom multiplier before / after the drop")]
        public Vector2 dropBloomScale = new Vector2(0.75f, 1f);

        [Tooltip("Set by the free camera while the user flies around; the director then stops moving the camera")]
        public bool userControl;

        ShowSection[] _sections = new ShowSection[0];
        Segment[] _segments = new Segment[0];
        float _endTime = 60f;
        double _drop = -1;
        double _hitTime = -999;
        float _hitStrength;
        Bloom _bloom;
        ColorAdjustments _color;

        public static Shot DefaultShot => MakeShot("wide", new Vector3(0, 10, -205), new Vector3(0, 62, 10), new Vector3(0, 12, -190), new Vector3(0, 66, 10), 52f);

        static Shot PreDropShot => MakeShot("slow push-in", new Vector3(0, 6, -265), new Vector3(0, 54, 10), new Vector3(0, 9, -180), new Vector3(0, 62, 10), 50f);
        static Shot DropShot => MakeShot("drop: low, tilted to the sky", new Vector3(0, 3, -120), new Vector3(0, 90, 15), new Vector3(0, 5, -160), new Vector3(0, 88, 15), 56f);

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

        /// <param name="drop">Song time of the drop, or a negative value for a song without one.</param>
        public void Setup(ShowSection[] sections, float endTime, double drop = -1)
        {
            _sections = sections ?? new ShowSection[0];
            _endTime = Mathf.Max(1f, endTime);
            _drop = drop > 0 ? drop : -1;
            BuildSegments();
            _hitTime = -999;
            _hitStrength = 0f;
            if (_bloom == null) FetchOverrides();
        }

        void BuildSegments()
        {
            var segs = new List<Segment>();
            var used = new Dictionary<string, int>();
            double afterDrop = double.MinValue;

            if (_drop > 0)
            {
                segs.Add(new Segment { start = 0, end = _drop, shot = PreDropShot, blendIn = 0f });
                afterDrop = _drop + dropHold;
                segs.Add(new Segment { start = _drop, end = afterDrop, shot = DropShot, blendIn = dropWhip, whip = true });
            }

            for (int i = 0; i < _sections.Length; i++)
            {
                var sec = _sections[i];
                string n = sec.name;
                used.TryGetValue(n, out int k);
                used[n] = k + 1;
                double start = sec.start;
                double end = i == _sections.Length - 1 ? Mathf.Max(sec.end, _endTime) : sec.end;
                if (_drop > 0)
                {
                    if (end <= afterDrop) continue; // swallowed by the build-up or the drop shot
                    start = System.Math.Max(start, afterDrop);
                }
                var vars = Variants(n);
                segs.Add(new Segment { start = start, end = end, shot = vars[k % vars.Count], blendIn = segs.Count == 0 ? 0f : blendTime });
            }

            if (segs.Count == 0) segs.Add(new Segment { start = 0, end = _endTime, shot = DefaultShot, blendIn = 0f });
            // The last segment runs to the end of the show.
            var last = segs[segs.Count - 1];
            if (last.end < _endTime) { last.end = _endTime; segs[segs.Count - 1] = last; }
            _segments = segs.ToArray();
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

        int SegmentIndex(double t)
        {
            int idx = 0;
            for (int i = 0; i < _segments.Length; i++)
                if (t >= _segments[i].start) idx = i;
            return idx;
        }

        static float Smooth(float u)
        {
            u = Mathf.Clamp01(u);
            return u * u * (3f - 2f * u);
        }

        static float EaseOut(float u)
        {
            u = Mathf.Clamp01(u);
            float r = 1f - u;
            return 1f - r * r * r;
        }

        void Pose(int i, double t, out Vector3 pos, out Vector3 look, out float fov)
        {
            var seg = _segments[i];
            float u = (float)((t - seg.start) / System.Math.Max(0.01, seg.end - seg.start));
            float w = Smooth(u);
            pos = Vector3.Lerp(seg.shot.posA, seg.shot.posB, w);
            look = Vector3.Lerp(seg.shot.lookA, seg.shot.lookB, w);
            fov = seg.shot.fov;
            // Slight sideways arc so the long build-up push-in is not a dead-straight dolly.
            if (_drop > 0 && i == 0) pos.x += Mathf.Sin(Mathf.Clamp01(u) * Mathf.PI) * -18f;
        }

        /// <summary>0 before the drop, 1 shortly after it (1 throughout for songs without a drop).</summary>
        float DropMix(double t)
        {
            if (_drop <= 0) return 1f;
            return Mathf.Clamp01((float)((t - _drop) / 0.25));
        }

        /// <summary>Combined flash/shake strength from hit cues and the drop at song time t.</summary>
        public float FlashAt(double t)
        {
            float hit = t < _hitTime ? 0f : _hitStrength * Mathf.Exp(-(float)(t - _hitTime) / hitDecay);
            if (_drop > 0 && t >= _drop)
                hit = Mathf.Max(hit, dropStrength * Mathf.Exp(-(float)(t - _drop) / dropDecay));
            return hit;
        }

        /// <summary>Place the camera for song time <paramref name="t"/> (may be negative during the title).</summary>
        public void Evaluate(double t)
        {
            if (cam == null || _segments.Length == 0) return;
            int i = SegmentIndex(t);
            Pose(i, t, out var pos, out var look, out float fov);
            var seg = _segments[i];
            if (i > 0 && seg.blendIn > 0f && t - seg.start < seg.blendIn)
            {
                Pose(i - 1, t, out var p0, out var l0, out float f0);
                float x = (float)((t - seg.start) / seg.blendIn);
                float w = seg.whip ? EaseOut(x) : Smooth(x);
                pos = Vector3.Lerp(p0, pos, w);
                look = Vector3.Lerp(l0, look, w);
                fov = Mathf.Lerp(f0, fov, w);
            }

            float hit = FlashAt(t);

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

            float dropMix = DropMix(t);
            if (_color != null)
            {
                float baseEv = Mathf.Lerp(preDropExposure, 0f, dropMix) * (_drop > 0 ? 1f : 0f);
                _color.postExposure.value = baseEv + Mathf.Min(flashExposure * hit, maxFlashExposure);
            }
            if (_bloom != null)
            {
                float scale = _drop > 0 ? Mathf.Lerp(dropBloomScale.x, dropBloomScale.y, dropMix) : 1f;
                _bloom.intensity.value = Mathf.Lerp(bloomRange.x, bloomRange.y, EnergyAt(t)) * scale;
            }
        }
    }
}
