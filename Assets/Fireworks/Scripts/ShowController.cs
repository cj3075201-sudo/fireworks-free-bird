using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Fireworks
{
    /// <summary>
    /// Runs the show from a cue sheet. The clock is the audio DSP clock (music starts with PlayScheduled),
    /// each aerial cue is launched at t − RiseTime(type, h) so it bursts exactly at t, and sounds are scheduled
    /// sample-accurately a little ahead of time. Fireworks are pooled per type.
    /// </summary>
    public class ShowController : MonoBehaviour, IFireworkListener
    {
        enum Stage { Idle, Running, Outro, Credits }

        [Header("Show data")]
        [Tooltip("Cue sheet JSON (Assets/Fireworks/Show/*.json or Dev/preview_cues.json)")]
        public TextAsset cueSheet;
        [Tooltip("Song. Leave empty to run on an internal clock.")]
        public AudioClip music;
        [Range(0f, 1f)] public float musicVolume = 0.85f;

        [Header("References")]
        public FireworkLibrary library;
        public ShowAudio sfx;
        public ShowCamera director;
        public ShowUI ui;
        public BurstLights burstLights;
        public AudioSource musicSource;

        [Header("Intro and outro")]
        public string title = "F I R E W O R K S";
        public string subtitle = "a particle show";
        [Tooltip("Seconds from black screen to the first beat of the song (raised automatically for long early rises)")]
        public float minLeadIn = 6f;
        public float fadeInTime = 2.2f;
        [Tooltip("After the song ends, wait at most this long for sparks and smoke to clear")]
        public float outroMaxWait = 14f;
        public float creditFadeTime = 2.5f;
        [Tooltip("Hint shown under the credit")]
        public string controlsHint = "R  replay      ESC  quit";

        [Header("Hit flash")]
        [Tooltip("Screen flash opacity for a scale-1 hit cue")]
        [Range(0f, 0.5f)] public float hitFlash = 0.03f;
        [Tooltip("Screen flash opacity at the drop (slow-burn songs). Kept low so the frame never burns white.")]
        [Range(0f, 0.5f)] public float dropFlash = 0.08f;
        [Tooltip("Decay of the drop screen flash, seconds")]
        public float dropFlashDecay = 0.2f;

        [Header("Random")]
        [Tooltip("Seed for per-cue variation (wobble, tilt, sound choice). Same seed = same show.")]
        public int seed = 1717;

        [Header("Debug")]
        [Tooltip("F1 toggles a small stats readout")]
        public bool showStats;

        public ShowData Data { get; private set; }
        public double SongTime => _clock;
        public float SongLength { get; private set; }
        public int LiveParticles { get; private set; }
        public int PeakParticles { get; private set; }
        public double PeakParticlesTime { get; private set; }
        public int ActiveFireworks => _active.Count;
        public readonly List<string> Warnings = new List<string>();

        struct Scheduled
        {
            public int index;
            public ShowCue cue;
            public double launch;
            public double burst;
            public double end;
        }

        struct SoundEvent
        {
            public double time;
            public SfxKind kind;
            public float volume;
            public float pitch;
            public float x;
            public bool accent; // burst of a hit cue: ShowAudio.hitAccent applies at play time
            public int loopOwner; // >=0 start loop, <=-1 stop loop of (-1 - owner), int.MinValue = one-shot
        }

        readonly List<Scheduled> _schedule = new List<Scheduled>();
        readonly List<SoundEvent> _sounds = new List<SoundEvent>();
        readonly List<Firework> _active = new List<Firework>();
        readonly Dictionary<FireworkType, Stack<Firework>> _pool = new Dictionary<FireworkType, Stack<Firework>>();
        Transform _poolRoot;
        int _next, _nextSound;
        double _clock, _leadIn, _dspStart, _endTime, _outroStart, _creditStart, _lastDsp;
        float _dspStall;
        bool _manual;
        Stage _stage = Stage.Idle;
        System.Random _rng, _soundRng;
        double _flashTime = -999;
        Color _flashColor = Color.white;
        float _flashAmount;

        const double SoundLookahead = 0.12;

        void Start()
        {
            Begin(false);
        }

        // ------------------------------------------------------------------ lifecycle

        /// <summary>Load the sheet, prewarm the pool and start the intro. Manual = edit-mode capture (no audio, caller steps time).</summary>
        public void Begin(bool manual)
        {
            _manual = manual;
            StopEverything();
            LoadSheet();

            _rng = new System.Random(seed);
            _soundRng = new System.Random(seed * 7 + 3);
            BuildSchedule();
            Prewarm();
            BuildSounds();

            double maxNeed = 0;
            foreach (var s in _schedule) maxNeed = Math.Max(maxNeed, -s.launch);
            _leadIn = Math.Max(minLeadIn, maxNeed + 0.5);
            _clock = -_leadIn;
            _next = 0;
            _nextSound = 0;
            PeakParticles = 0;

            float musicLen = music != null ? music.length : 0f;
            SongLength = Mathf.Max(musicLen, Data.duration > 0f ? Data.duration : 0f);
            if (SongLength <= 0f) SongLength = Data.LastEventTime();
            _endTime = Math.Max(SongLength, Data.LastEventTime());

            if (director != null) director.Setup(Data.sections, (float)_endTime, Data.HasDrop ? Data.drop : -1.0);
            if (ui != null)
            {
                ui.SetTitle(title, subtitle);
                ui.SetCredit(Data.credit ?? "");
                if (ui.hintText != null) ui.hintText.text = controlsHint;
                ui.SetAlphas(1f, 0f, 0f);
                ui.SetFlash(Color.white, 0f);
            }
            if (burstLights != null) burstLights.Clear();

            if (!_manual)
            {
                _lastDsp = AudioSettings.dspTime;
                _dspStall = 0f;
                _dspStart = AudioSettings.dspTime + _leadIn;
                if (musicSource != null && music != null)
                {
                    musicSource.clip = music;
                    musicSource.volume = musicVolume;
                    musicSource.loop = false;
                    musicSource.PlayScheduled(_dspStart);
                }
            }
            _stage = Stage.Running;
            foreach (var w in Warnings) Debug.LogWarning("[Fireworks] " + w);
        }

        void LoadSheet()
        {
            Warnings.Clear();
            Data = cueSheet != null ? ShowData.Parse(cueSheet.text, Warnings) : new ShowData();
            if (cueSheet == null) Warnings.Add("No cue sheet assigned");
        }

        void StopEverything()
        {
            foreach (var fw in _active)
            {
                fw.ResetToPool();
                Return(fw);
            }
            _active.Clear();
            if (musicSource != null) musicSource.Stop();
            if (sfx != null && !_manual) sfx.StopAll();
            if (burstLights != null) burstLights.Clear();
            if (director != null) director.userControl = false;
            _flashAmount = 0f;
            _flashTime = -999;
        }

        // ------------------------------------------------------------------ schedule

        void BuildSchedule()
        {
            _schedule.Clear();
            for (int i = 0; i < Data.cues.Length; i++)
            {
                var c = Data.cues[i];
                bool aerial = FireworkCatalog.IsAerial(c.Type);
                double rise = FireworkCatalog.RiseTime(c.Type, c.h);
                _schedule.Add(new Scheduled
                {
                    index = i,
                    cue = c,
                    launch = aerial ? c.t - rise : c.t,
                    burst = c.t,
                    end = aerial ? c.t : c.t + c.dur,
                });
            }
            _schedule.Sort((a, b) => a.launch.CompareTo(b.launch));
        }

        void BuildSounds()
        {
            _sounds.Clear();
            var r = _soundRng;
            foreach (var s in _schedule)
            {
                var c = s.cue;
                // Relative loudness per shell; ShowAudio applies the per-kind mix gains on top.
                // Scale only turns small shells down: 0.6 → 0.6, ≥1.0 → 1.0.
                float loud = Mathf.Lerp(0.6f, 1f, Mathf.InverseLerp(ShowData.MinScale, 1f, c.scale));
                if (c.Type == FireworkType.Fountain)
                {
                    _sounds.Add(new SoundEvent { time = s.burst, loopOwner = s.index, volume = loud, x = c.x });
                    _sounds.Add(new SoundEvent { time = s.end, loopOwner = -1 - s.index, x = c.x });
                    continue;
                }

                // Launch thump, and a whistle on comets and on some tall shells.
                _sounds.Add(OneShot(s.launch, SfxKind.Launch, loud, Pitch(r, 0.9f, 1.1f), c.x));
                bool whistle = c.Type == FireworkType.Comet || (c.h >= 85f && r.NextDouble() < 0.2);
                if (whistle) _sounds.Add(OneShot(s.launch + 0.05, SfxKind.Whistle, loud, Pitch(r, 0.92f, 1.08f), c.x));

                if (c.Type == FireworkType.Comet) continue;

                bool small = c.Type == FireworkType.Ring || c.scale < 1f;
                // Bigger shells sound a little deeper.
                float pitch = Pitch(r, 0.9f, 1.1f) * Mathf.Lerp(1.08f, 0.9f, Mathf.InverseLerp(ShowData.MinScale, ShowData.MaxScale, c.scale));
                _sounds.Add(OneShot(s.burst, small ? SfxKind.Pop : SfxKind.Boom, loud, pitch, c.x, c.hit));

                switch (c.Type)
                {
                    case FireworkType.Crossette:
                        for (int k = 0; k < 3; k++)
                            _sounds.Add(OneShot(s.burst + 1.0 + k * 0.07 + r.NextDouble() * 0.03, SfxKind.Pop, loud, Pitch(r, 1.0f, 1.25f), c.x));
                        break;
                    case FireworkType.Strobe:
                        _sounds.Add(OneShot(s.burst + 0.3, SfxKind.Crackle, loud, Pitch(r, 0.95f, 1.05f), c.x));
                        break;
                    case FireworkType.Willow:
                        _sounds.Add(OneShot(s.burst + 2.0, SfxKind.Crackle, loud, Pitch(r, 0.85f, 0.95f), c.x));
                        break;
                }
            }
            _sounds.Sort((a, b) => a.time.CompareTo(b.time));
        }

        static float Pitch(System.Random r, float lo, float hi) => Mathf.Lerp(lo, hi, (float)r.NextDouble());

        static SoundEvent OneShot(double t, SfxKind kind, float vol, float pitch, float x, bool accent = false)
        {
            return new SoundEvent { time = t, kind = kind, volume = vol, pitch = pitch, x = x, accent = accent, loopOwner = int.MinValue };
        }

        // ------------------------------------------------------------------ pool

        void Prewarm()
        {
            if (_poolRoot == null)
            {
                var go = new GameObject("Firework Pool");
                go.transform.SetParent(transform, false);
                _poolRoot = go.transform;
            }

            // Peak overlap per type, counting the smoke tail.
            var need = new int[FireworkCatalog.TypeCount];
            var events = new List<(double t, int type, int delta)>();
            foreach (var s in _schedule)
            {
                int ti = (int)s.cue.Type;
                double life = s.end + FireworkCatalog.AfterBurstLife(s.cue.Type) + FireworkCatalog.SmokeLife * 0.5;
                events.Add((s.launch, ti, +1));
                events.Add((life, ti, -1));
            }
            events.Sort((a, b) => a.t != b.t ? a.t.CompareTo(b.t) : a.delta.CompareTo(b.delta));
            var cur = new int[FireworkCatalog.TypeCount];
            foreach (var e in events)
            {
                cur[e.type] += e.delta;
                need[e.type] = Math.Max(need[e.type], cur[e.type]);
            }

            for (int i = 0; i < FireworkCatalog.TypeCount; i++)
            {
                var type = (FireworkType)i;
                var stack = Stack(type);
                int want = Mathf.Min(need[i], 48);
                while (stack.Count < want)
                {
                    var fw = Create(type);
                    if (fw == null) break;
                    stack.Push(fw);
                }
            }
        }

        Stack<Firework> Stack(FireworkType type)
        {
            if (!_pool.TryGetValue(type, out var s))
            {
                s = new Stack<Firework>();
                _pool[type] = s;
            }
            return s;
        }

        Firework Create(FireworkType type)
        {
            var prefab = library != null ? library.Prefab(type) : null;
            if (prefab == null) return null;
            var fw = Instantiate(prefab, _poolRoot);
            fw.name = prefab.name;
            fw.ResetToPool();
            return fw;
        }

        Firework Get(FireworkType type)
        {
            var s = Stack(type);
            return s.Count > 0 ? s.Pop() : Create(type);
        }

        void Return(Firework fw) => Stack(fw.type).Push(fw);

        // ------------------------------------------------------------------ running

        void Update()
        {
            HandleKeys();
            if (_manual || _stage == Stage.Idle) return;
            Step(ReadClock(), Time.deltaTime);
        }

        void HandleKeys()
        {
            var kb = Keyboard.current;
            if (kb == null) return;
            if (kb.rKey.wasPressedThisFrame && !_manual) Begin(false);
            if (kb.escapeKey.wasPressedThisFrame) Quit();
            if (kb.f1Key.wasPressedThisFrame) showStats = !showStats;
        }

        static void Quit()
        {
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#else
            Application.Quit();
#endif
        }

        double ReadClock()
        {
            double dsp = AudioSettings.dspTime;
            if (dsp != _lastDsp)
            {
                _lastDsp = dsp;
                _dspStall = 0f;
            }
            else
            {
                _dspStall += Time.unscaledDeltaTime;
            }

            _clock += Time.unscaledDeltaTime;
            if (_dspStall < 0.5f)
            {
                // Follow the audio clock; dspTime moves in buffer-sized steps, so ease toward it.
                double target = dsp - _dspStart;
                double err = target - _clock;
                if (Math.Abs(err) > 0.08) _clock = target;
                else _clock += err * 0.2;
            }
            return _clock;
        }

        /// <summary>Edit-mode capture: advance the whole show by <paramref name="dt"/> and simulate particles by hand.</summary>
        public void ManualStep(float dt)
        {
            if (!_manual) return;
            Step(_clock + dt, dt);
        }

        void Step(double now, float dt)
        {
            _clock = now;

            while (_next < _schedule.Count && _schedule[_next].launch <= now)
                Spawn(_schedule[_next++]);

            for (int i = _active.Count - 1; i >= 0; i--)
            {
                var fw = _active[i];
                fw.Tick(now);
                if (_manual && !fw.IsDone) fw.SimulateStep(dt);
                if (fw.IsDone)
                {
                    _active.RemoveAt(i);
                    Return(fw);
                }
            }

            if (!_manual) DispatchSounds(now);

            int live = 0;
            for (int i = 0; i < _active.Count; i++) live += _active[i].LiveParticles();
            LiveParticles = live;
            if (live > PeakParticles)
            {
                PeakParticles = live;
                PeakParticlesTime = now;
            }

            if (director != null) director.Evaluate(now);
            if (burstLights != null) burstLights.Evaluate(now);
            UpdateStage(now);
        }

        void Spawn(Scheduled s)
        {
            var fw = Get(s.cue.Type);
            if (fw == null) return;
            var c = s.cue;
            var origin = new Vector3(c.x, 0.2f, c.z);
            float driftX = ((float)_rng.NextDouble() * 2f - 1f) * 2.5f;
            float driftZ = ((float)_rng.NextDouble() * 2f - 1f) * 1.5f;
            var apex = new Vector3(c.x + driftX, c.h, c.z + driftZ);
            var pal = library != null ? library.Palette(c.palette) : Palettes.Defaults()[0];
            fw.Launch(c, s.index, pal, s.launch, s.burst, s.end, origin, apex, _rng, this, _manual);
            _active.Add(fw);
        }

        void DispatchSounds(double now)
        {
            if (sfx == null) { _nextSound = _sounds.Count; return; }
            while (_nextSound < _sounds.Count && _sounds[_nextSound].time <= now + SoundLookahead)
            {
                var e = _sounds[_nextSound++];
                if (e.time < now - 0.25) continue; // too late (after a hitch); skip rather than smear
                double dsp = _dspStart + e.time;
                if (e.loopOwner == int.MinValue) sfx.PlayAt(e.kind, dsp, e.volume, e.x, e.pitch, _soundRng, e.accent);
                else if (e.loopOwner >= 0) sfx.StartLoop(e.loopOwner, dsp, e.volume, e.x);
                else sfx.StopLoop(-1 - e.loopOwner, dsp);
            }
        }

        public void OnFireworkBurst(Firework fw)
        {
            var c = fw.Cue;
            if (c == null) return;
            double t = c.t;
            Color col = fw.Palette.mixed ? new Color(1f, 0.85f, 0.7f) : fw.Palette.primary;
            if (burstLights != null && fw.lightStrength > 0f)
            {
                Vector3 p = fw.type == FireworkType.Fountain ? fw.Origin + Vector3.up * 3f : fw.Apex;
                burstLights.Flash(p, col, fw.lightStrength * c.scale, t);
            }
            if (c.hit)
            {
                if (director != null) director.Hit(t, Mathf.Clamp(c.scale, 0.6f, 1.6f));
                _flashTime = t;
                _flashColor = Color.Lerp(col, new Color(0.85f, 0.9f, 1f), 0.75f);
                _flashAmount = hitFlash * c.scale;
            }
        }

        void UpdateStage(double now)
        {
            float fade = 0f, titleA = 0f, creditA = 0f;
            double start = -_leadIn;

            // Fade in from black, title, then the song.
            fade = 1f - Mathf.Clamp01((float)((now - start) / fadeInTime));
            float tIn = Mathf.Clamp01((float)((now - (start + 0.8)) / 1.2));
            float tOut = 1f - Mathf.Clamp01((float)((now - (-1.8)) / 1.2));
            titleA = Mathf.Min(tIn, tOut);

            switch (_stage)
            {
                case Stage.Running:
                    if (now >= _endTime && _next >= _schedule.Count)
                    {
                        _stage = Stage.Outro;
                        _outroStart = now;
                    }
                    break;
                case Stage.Outro:
                    if (_active.Count == 0 || now - _outroStart > outroMaxWait)
                    {
                        _stage = Stage.Credits;
                        _creditStart = now;
                    }
                    break;
                case Stage.Credits:
                    creditA = Mathf.Clamp01((float)((now - _creditStart) / creditFadeTime));
                    fade = 0.65f * creditA;
                    break;
            }

            if (ui != null)
            {
                ui.SetAlphas(fade, titleA, creditA);
                float f = now >= _flashTime ? _flashAmount * Mathf.Exp(-Mathf.Max(0f, (float)(now - _flashTime)) / 0.09f) : 0f;
                var tint = _flashColor;
                if (Data != null && Data.HasDrop && now >= Data.drop)
                {
                    float d = dropFlash * Mathf.Exp(-(float)(now - Data.drop) / dropFlashDecay);
                    if (d > f) { f = d; tint = new Color(0.9f, 0.93f, 1f); }
                }
                ui.SetFlash(tint, f);
            }
        }

        public bool IsFinished => _stage == Stage.Credits;
        public double LeadIn => _leadIn;
        public double EndTime => _endTime;

        void OnGUI()
        {
            if (!showStats) return;
            GUI.Label(new Rect(12, 12, 520, 22), $"t {_clock:0.00}s   fireworks {_active.Count}   particles {LiveParticles} (peak {PeakParticles} @ {PeakParticlesTime:0.0}s)   {1f / Mathf.Max(1e-4f, Time.smoothDeltaTime):0} fps");
        }
    }
}
