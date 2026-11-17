using System;
using UnityEngine;

namespace Fireworks
{
    /// <summary>How the cue palette tints one particle system of a firework.</summary>
    public enum ColorRole
    {
        [Tooltip("Keep the colour authored in the prefab")] Fixed,
        [Tooltip("Palette primary colour")] Primary,
        [Tooltip("Palette secondary colour")] Secondary,
        [Tooltip("Start white-hot, shift primary → secondary over lifetime")] PrimaryToSecondary,
        [Tooltip("Gold, tinted a little toward the palette primary")] WarmTint,
    }

    /// <summary>One particle system of a firework plus the rules for applying palette and scale to it.</summary>
    [Serializable]
    public class FxPart
    {
        public ParticleSystem system;
        public ColorRole role = ColorRole.Primary;
        [Tooltip("0 = pure palette colour, 1 = white")]
        [Range(0f, 1f)] public float whiteMix;
        [Tooltip("Multiply start speed by cue scale (burst radius grows with scale)")]
        public bool scaleSpeed = true;
        [Tooltip("Multiply emission counts by cue scale")]
        public bool scaleCount = true;
        [Tooltip("Start size multiplier = scale ^ sizeExponent (0 = no change, 1 = linear)")]
        [Range(0f, 1f)] public float sizeExponent = 0.5f;

        [NonSerialized] public bool cached;
        [NonSerialized] public ParticleSystem.MinMaxCurve baseSpeed;
        [NonSerialized] public ParticleSystem.MinMaxCurve baseSize;
        [NonSerialized] public ParticleSystem.MinMaxCurve baseRate;
        [NonSerialized] public ParticleSystem.Burst[] baseBursts;
        [NonSerialized] public ParticleSystem.MinMaxGradient baseColor;
        [NonSerialized] public float baseAlpha;
        [NonSerialized] public GradientAlphaKey[] baseLifetimeAlpha;
    }

    public interface IFireworkListener
    {
        /// <summary>Called on the frame an aerial firework bursts, or a fountain starts.</summary>
        void OnFireworkBurst(Firework fw);
    }

    /// <summary>
    /// Runtime driver of one pooled firework. The rising shell is moved by script along an analytic path,
    /// so the burst happens on exactly the frame the show clock passes the cue time.
    /// Secondary effects (crossette splits, willow glitter, palm sparks) are particle sub-emitters.
    /// </summary>
    public class Firework : MonoBehaviour
    {
        public enum Phase { Idle, Rising, Burst, Fountain, Fading }

        [Tooltip("Cue type this prefab renders")]
        public FireworkType type;

        [Header("Rise")]
        [Tooltip("Moved along the launch path while rising")]
        public Transform head;
        [Tooltip("Top-level systems under Head. Emission is switched off at the burst.")]
        public ParticleSystem[] headSystems = new ParticleSystem[0];
        [Tooltip("Bright local-space dot on the shell, cleared at the burst")]
        public ParticleSystem headCore;
        [Tooltip("Sideways wobble of the rising shell, metres")]
        public float wobble = 0.5f;

        [Header("Burst")]
        [Tooltip("Placed at the apex (aerial) or on the launch pad (fountain) before the burst systems play")]
        public Transform burstRoot;
        [Tooltip("Top-level systems under BurstRoot. Their sub-emitters play with them.")]
        public ParticleSystem[] burstSystems = new ParticleSystem[0];
        [Tooltip("Random burst orientation range in degrees (x tilt, y yaw). Used by ring and crossette.")]
        public Vector2 tiltRange;
        [Tooltip("Random roll around the view axis")]
        public bool randomRoll;

        [Header("Palette / scale")]
        public FxPart[] parts = new FxPart[0];

        [Header("Timing")]
        [Tooltip("Seconds after the burst when secondary breaks happen (crossette split). 0 = none.")]
        public float secondaryDelay;
        [Tooltip("Relative brightness of the light this burst throws on water and city")]
        public float lightStrength = 1f;
        [Tooltip("Optional light kept on while a fountain burns (palette coloured, flickers)")]
        public Light sustainLight;
        [Tooltip("Peak intensity of the sustain light at scale 1")]
        public float sustainIntensity = 250f;

        public Phase CurrentPhase { get; private set; } = Phase.Idle;
        public ShowCue Cue { get; private set; }
        public PaletteColors Palette { get; private set; }
        public int CueIndex { get; private set; }
        public double LaunchTime { get; private set; }
        public double BurstTime { get; private set; }
        public double EndTime { get; private set; }
        public Vector3 Origin { get; private set; }
        public Vector3 Apex { get; private set; }
        public bool IsDone => CurrentPhase == Phase.Idle;

        ParticleSystem[] _all;
        bool[] _headEmission;
        bool[] _burstEmission;
        bool[] _headPending;
        bool[] _burstPending;
        bool _headRunning, _burstRunning;
        bool _manual;
        float _wobblePhase;
        IFireworkListener _listener;
        bool _init;

        void EnsureInit()
        {
            if (_init) return;
            _init = true;
            _all = GetComponentsInChildren<ParticleSystem>(true);
            _headEmission = new bool[headSystems.Length];
            _headPending = new bool[headSystems.Length];
            for (int i = 0; i < headSystems.Length; i++) _headEmission[i] = headSystems[i].emission.enabled;
            _burstEmission = new bool[burstSystems.Length];
            _burstPending = new bool[burstSystems.Length];
            for (int i = 0; i < burstSystems.Length; i++) _burstEmission[i] = burstSystems[i].emission.enabled;
            foreach (var p in parts) Cache(p);
        }

        static void Cache(FxPart p)
        {
            if (p == null || p.system == null || p.cached) return;
            var main = p.system.main;
            var em = p.system.emission;
            p.baseSpeed = main.startSpeed;
            p.baseSize = main.startSize;
            p.baseColor = main.startColor;
            p.baseAlpha = BaseAlpha(main.startColor);
            p.baseRate = em.rateOverTime;
            p.baseBursts = new ParticleSystem.Burst[em.burstCount];
            em.GetBursts(p.baseBursts);
            var col = p.system.colorOverLifetime;
            p.baseLifetimeAlpha = col.enabled && col.color.gradient != null ? col.color.gradient.alphaKeys : null;
            p.cached = true;
        }

        static float BaseAlpha(ParticleSystem.MinMaxGradient g)
        {
            switch (g.mode)
            {
                case ParticleSystemGradientMode.Color: return g.color.a;
                case ParticleSystemGradientMode.TwoColors: return Mathf.Max(g.colorMin.a, g.colorMax.a);
                default: return 1f;
            }
        }

        /// <summary>Stop and clear everything; the object is ready for reuse.</summary>
        public void ResetToPool()
        {
            EnsureInit();
            foreach (var ps in headSystems) ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            foreach (var ps in burstSystems) ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            for (int i = 0; i < headSystems.Length; i++) SetEmission(headSystems[i], _headEmission[i]);
            for (int i = 0; i < burstSystems.Length; i++) SetEmission(burstSystems[i], _burstEmission[i]);
            _headRunning = _burstRunning = false;
            if (sustainLight != null) sustainLight.enabled = false;
            CurrentPhase = Phase.Idle;
            Cue = null;
            _listener = null;
        }

        /// <summary>
        /// Start this firework. Times are song times in seconds. For aerial types the burst lands exactly on
        /// <paramref name="burstTime"/>; for fountains <paramref name="burstTime"/> is the start and
        /// <paramref name="endTime"/> the end of emission.
        /// </summary>
        /// <param name="manual">True when the caller advances particles itself with <see cref="SimulateStep"/> (edit-mode captures).</param>
        public void Launch(ShowCue cue, int cueIndex, PaletteColors palette, double launchTime, double burstTime, double endTime,
            Vector3 origin, Vector3 apex, System.Random rng, IFireworkListener listener, bool manual)
        {
            ResetToPool();
            Cue = cue;
            CueIndex = cueIndex;
            Palette = palette;
            LaunchTime = launchTime;
            BurstTime = burstTime;
            EndTime = endTime;
            Origin = origin;
            Apex = apex;
            _listener = listener;
            _manual = manual;
            _wobblePhase = (float)(rng.NextDouble() * Math.PI * 2.0);

            ApplyLook(palette, cue != null ? cue.scale : 1f, rng);
            if (sustainLight != null)
                sustainLight.color = palette.mixed ? new Color(1f, 0.8f, 0.6f) : Color.Lerp(palette.primary, Color.white, 0.3f);

            if (burstRoot != null)
            {
                float tx = ((float)rng.NextDouble() * 2f - 1f) * tiltRange.x;
                float ty = ((float)rng.NextDouble() * 2f - 1f) * tiltRange.y;
                float rz = randomRoll ? (float)rng.NextDouble() * 360f : 0f;
                burstRoot.SetPositionAndRotation(type == FireworkType.Fountain ? origin : apex, Quaternion.Euler(tx, ty, rz));
            }

            if (type == FireworkType.Fountain)
            {
                CurrentPhase = Phase.Fountain;
                StartBurstSystems();
                _listener?.OnFireworkBurst(this);
            }
            else
            {
                if (head != null) head.SetPositionAndRotation(origin, Quaternion.identity);
                StartGroup(headSystems, _headPending);
                _headRunning = true;
                CurrentPhase = Phase.Rising;
            }
        }

        /// <summary>Advance the firework's own timeline to song time <paramref name="now"/>.</summary>
        public void Tick(double now)
        {
            UpdateSustainLight(now);
            switch (CurrentPhase)
            {
                case Phase.Rising:
                {
                    double span = Math.Max(1e-3, BurstTime - LaunchTime);
                    float s = Mathf.Clamp01((float)((now - LaunchTime) / span));
                    if (head != null) head.position = ShellPosition(s);
                    if (now >= BurstTime) Burst();
                    break;
                }
                case Phase.Fountain:
                    if (now >= EndTime)
                    {
                        for (int i = 0; i < burstSystems.Length; i++) SetEmission(burstSystems[i], false);
                        CurrentPhase = Phase.Fading;
                    }
                    break;
                case Phase.Burst:
                case Phase.Fading:
                {
                    double since = now - (CurrentPhase == Phase.Fading && type == FireworkType.Fountain ? EndTime : BurstTime);
                    if (since > 0.5 && LiveParticles() == 0) ResetToPool();
                    break;
                }
            }
        }

        void UpdateSustainLight(double now)
        {
            if (sustainLight == null) return;
            float k = 0f;
            float scale = Cue != null ? Cue.scale : 1f;
            if (CurrentPhase == Phase.Fountain)
                k = Mathf.Clamp01((float)(now - BurstTime) / 0.3f);
            else if (CurrentPhase == Phase.Fading && type == FireworkType.Fountain)
                k = Mathf.Clamp01(1f - (float)(now - EndTime) / 0.8f);
            float flicker = 0.85f + 0.15f * Mathf.PerlinNoise((float)now * 9f, CueIndex * 0.37f);
            sustainLight.enabled = k > 0.001f;
            sustainLight.intensity = sustainIntensity * scale * k * flicker;
        }

        /// <summary>Edit-mode only: advance all running particle systems by <paramref name="dt"/> seconds.</summary>
        public void SimulateStep(float dt)
        {
            if (_headRunning) SimGroup(headSystems, _headPending, dt);
            if (_burstRunning) SimGroup(burstSystems, _burstPending, dt);
        }

        public Vector3 ShellPosition(float s)
        {
            // Decelerating climb: vertical speed reaches zero exactly at the apex.
            float k = 1f - (1f - s) * (1f - s);
            Vector3 p = Vector3.Lerp(Origin, Apex, s);
            p.y = Mathf.Lerp(Origin.y, Apex.y, k);
            p.x += Mathf.Sin(s * 7f + _wobblePhase) * wobble * s;
            return p;
        }

        void Burst()
        {
            if (head != null) head.position = Apex;
            for (int i = 0; i < headSystems.Length; i++) SetEmission(headSystems[i], false);
            if (headCore != null) headCore.Clear(false);
            StartBurstSystems();
            CurrentPhase = Phase.Burst;
            _listener?.OnFireworkBurst(this);
        }

        void StartBurstSystems()
        {
            StartGroup(burstSystems, _burstPending);
            _burstRunning = true;
        }

        void StartGroup(ParticleSystem[] systems, bool[] pending)
        {
            for (int i = 0; i < systems.Length; i++)
            {
                if (_manual) pending[i] = true;
                else systems[i].Play(true);
            }
        }

        static void SimGroup(ParticleSystem[] systems, bool[] pending, float dt)
        {
            for (int i = 0; i < systems.Length; i++)
            {
                systems[i].Simulate(dt, true, pending[i], false);
                pending[i] = false;
            }
        }

        static void SetEmission(ParticleSystem ps, bool on)
        {
            var em = ps.emission;
            em.enabled = on;
        }

        public int LiveParticles()
        {
            EnsureInit();
            int n = 0;
            for (int i = 0; i < _all.Length; i++) n += _all[i].particleCount;
            return n;
        }

        // ---------------------------------------------------------------- palette and scale

        void ApplyLook(PaletteColors pal, float scale, System.Random rng)
        {
            foreach (var p in parts)
            {
                if (p == null || p.system == null) continue;
                var ps = p.system;
                var main = ps.main;

                main.startSpeed = p.scaleSpeed ? Scale(p.baseSpeed, scale) : p.baseSpeed;
                main.startSize = Scale(p.baseSize, Mathf.Pow(scale, p.sizeExponent));

                var em = ps.emission;
                if (p.scaleCount)
                {
                    em.rateOverTime = Scale(p.baseRate, scale);
                    for (int i = 0; i < p.baseBursts.Length; i++)
                    {
                        var b = p.baseBursts[i];
                        b.count = Scale(b.count, scale);
                        em.SetBurst(i, b);
                    }
                }

                ApplyColor(p, pal);
            }
        }

        void ApplyColor(FxPart p, PaletteColors pal)
        {
            if (p.role == ColorRole.Fixed) return;
            var main = p.system.main;
            float a = p.baseAlpha;
            Color prim = WithAlpha(Color.Lerp(pal.primary, Color.white, p.whiteMix), a);
            Color sec = WithAlpha(Color.Lerp(pal.secondary, Color.white, p.whiteMix), a);
            var gold = new Color(1f, 0.6f, 0.2f, a);

            switch (p.role)
            {
                case ColorRole.Primary:
                    main.startColor = pal.mixed ? MixGradient(pal, p.whiteMix, a) : new ParticleSystem.MinMaxGradient(prim, Color.Lerp(prim, sec, 0.3f));
                    break;
                case ColorRole.Secondary:
                    main.startColor = pal.mixed ? MixGradient(pal, p.whiteMix, a) : new ParticleSystem.MinMaxGradient(sec, Color.Lerp(sec, prim, 0.2f));
                    break;
                case ColorRole.WarmTint:
                {
                    Color tint = pal.mixed ? gold : WithAlpha(Color.Lerp(gold, pal.primary, 0.35f), a);
                    main.startColor = new ParticleSystem.MinMaxGradient(gold, tint);
                    break;
                }
                case ColorRole.PrimaryToSecondary:
                {
                    var col = p.system.colorOverLifetime;
                    if (pal.mixed)
                    {
                        main.startColor = MixGradient(pal, p.whiteMix, a);
                        col.color = Gradient2(Color.white, Color.white, Color.white, p.baseLifetimeAlpha);
                    }
                    else
                    {
                        main.startColor = new ParticleSystem.MinMaxGradient(new Color(1f, 1f, 1f, a));
                        Color hot = Color.Lerp(pal.primary, Color.white, 0.55f);
                        col.color = Gradient2(hot, Color.Lerp(pal.primary, Color.white, p.whiteMix), Color.Lerp(pal.secondary, Color.white, p.whiteMix), p.baseLifetimeAlpha);
                    }
                    break;
                }
            }
        }

        static Color WithAlpha(Color c, float a)
        {
            c.a = a;
            return c;
        }

        static ParticleSystem.MinMaxGradient MixGradient(PaletteColors pal, float whiteMix, float alpha)
        {
            var cols = pal.mix != null && pal.mix.Length > 0 ? pal.mix : new[] { pal.primary, pal.secondary };
            var g = new Gradient { mode = GradientMode.Fixed };
            int n = Mathf.Min(cols.Length, 8);
            var keys = new GradientColorKey[n];
            for (int i = 0; i < n; i++)
                keys[i] = new GradientColorKey(Color.Lerp(cols[i], Color.white, whiteMix), (i + 1f) / n);
            g.SetKeys(keys, new[] { new GradientAlphaKey(alpha, 0f), new GradientAlphaKey(alpha, 1f) });
            return new ParticleSystem.MinMaxGradient(g) { mode = ParticleSystemGradientMode.RandomColor };
        }

        static ParticleSystem.MinMaxGradient Gradient2(Color c0, Color c1, Color c2, GradientAlphaKey[] alpha)
        {
            var g = new Gradient();
            g.SetKeys(
                new[] { new GradientColorKey(c0, 0f), new GradientColorKey(c1, 0.18f), new GradientColorKey(c1, 0.5f), new GradientColorKey(c2, 1f) },
                alpha ?? new[] { new GradientAlphaKey(1f, 0f), new GradientAlphaKey(1f, 0.7f), new GradientAlphaKey(0f, 1f) });
            return new ParticleSystem.MinMaxGradient(g);
        }

        static ParticleSystem.MinMaxCurve Scale(ParticleSystem.MinMaxCurve c, float k)
        {
            switch (c.mode)
            {
                case ParticleSystemCurveMode.Constant:
                    return new ParticleSystem.MinMaxCurve(c.constant * k);
                case ParticleSystemCurveMode.TwoConstants:
                    return new ParticleSystem.MinMaxCurve(c.constantMin * k, c.constantMax * k);
                default:
                    c.curveMultiplier *= k;
                    return c;
            }
        }
    }
}
