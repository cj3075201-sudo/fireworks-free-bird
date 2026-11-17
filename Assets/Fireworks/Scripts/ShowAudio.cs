using System.Collections.Generic;
using UnityEngine;

namespace Fireworks
{
    public enum SfxKind { Boom, Pop, Crackle, Launch, Whistle }

    /// <summary>
    /// Sample-accurate one-shot and loop playback with a fixed voice budget per sound kind.
    /// Missing clips are skipped silently, so the show runs before the WAV files exist.
    /// </summary>
    public class ShowAudio : MonoBehaviour
    {
        [Tooltip("Clip references. Fireworks/Build All relinks them from Assets/Fireworks/Audio/SFX by contract file name.")]
        public SfxBank bank;

        [Header("Voice budget (simultaneous sounds per kind)")]
        [Range(1, 16)] public int boomVoices = 6;
        [Range(1, 16)] public int popVoices = 6;
        [Range(1, 8)] public int crackleVoices = 3;
        [Range(1, 16)] public int launchVoices = 5;
        [Range(1, 8)] public int whistleVoices = 3;
        [Range(1, 8)] public int loopVoices = 3;

        [Header("Mix")]
        [Tooltip("Master SFX gain. The WAVs are pre-mixed for 1.0.")]
        [Range(0f, 1f)] public float sfxVolume = 1f;
        [Tooltip("How far sounds pan with the firework's x position (0 = centred)")]
        [Range(0f, 1f)] public float panWidth = 0.6f;

        class Voice
        {
            public AudioSource src;
            public double start;
            public double busyUntil;
            public float fadeFrom;
            public double fadeStart = -1, fadeEnd;
            public int owner = -1;
        }

        readonly Dictionary<SfxKind, Voice[]> _voices = new Dictionary<SfxKind, Voice[]>();
        Voice[] _loops;
        bool _ready;

        void Awake() => EnsureVoices();

        void EnsureVoices()
        {
            if (_ready) return;
            _ready = true;
            _voices[SfxKind.Boom] = MakeVoices("Boom", boomVoices, 64);
            _voices[SfxKind.Pop] = MakeVoices("Pop", popVoices, 96);
            _voices[SfxKind.Crackle] = MakeVoices("Crackle", crackleVoices, 110);
            _voices[SfxKind.Launch] = MakeVoices("Launch", launchVoices, 120);
            _voices[SfxKind.Whistle] = MakeVoices("Whistle", whistleVoices, 120);
            _loops = MakeVoices("Loop", loopVoices, 100);
        }

        Voice[] MakeVoices(string label, int count, int priority)
        {
            var arr = new Voice[count];
            for (int i = 0; i < count; i++)
            {
                var go = new GameObject($"{label} Voice {i}");
                go.transform.SetParent(transform, false);
                var src = go.AddComponent<AudioSource>();
                src.playOnAwake = false;
                src.spatialBlend = 0f;
                src.priority = priority;
                src.dopplerLevel = 0f;
                arr[i] = new Voice { src = src };
            }
            return arr;
        }

        AudioClip[] Clips(SfxKind kind)
        {
            if (bank == null) return null;
            switch (kind)
            {
                case SfxKind.Boom: return bank.boom;
                case SfxKind.Pop: return bank.pop;
                case SfxKind.Crackle: return bank.crackle;
                case SfxKind.Launch: return bank.launch;
                default: return bank.whistle;
            }
        }

        /// <summary>Schedule a one-shot at absolute DSP time. Returns false when no clip exists for the kind.</summary>
        public bool PlayAt(SfxKind kind, double dspTime, float volume, float x, float pitch, System.Random rng)
        {
            EnsureVoices();
            var clips = Clips(kind);
            if (clips == null || clips.Length == 0) return false;
            var clip = clips[rng.Next(clips.Length)];
            if (clip == null) return false;

            var voice = PickVoice(_voices[kind], dspTime);
            var src = voice.src;
            src.Stop();
            src.clip = clip;
            src.loop = false;
            src.pitch = pitch;
            src.volume = Mathf.Clamp01(volume * sfxVolume);
            src.panStereo = Mathf.Clamp(x / ShowData.MaxX, -1f, 1f) * panWidth;
            src.PlayScheduled(System.Math.Max(dspTime, AudioSettings.dspTime));
            voice.start = dspTime;
            voice.busyUntil = dspTime + clip.length / Mathf.Max(0.05f, pitch);
            voice.fadeStart = -1;
            return true;
        }

        static Voice PickVoice(Voice[] voices, double at)
        {
            Voice best = voices[0];
            foreach (var v in voices)
            {
                if (v.busyUntil <= at) return v;
                if (v.start < best.start) best = v; // steal the oldest
            }
            return best;
        }

        /// <summary>Start the fountain loop for <paramref name="owner"/> at DSP time; fades in briefly.</summary>
        public void StartLoop(int owner, double dspTime, float volume, float x)
        {
            EnsureVoices();
            var clip = bank != null ? bank.fountainLoop : null;
            if (clip == null) return;
            var voice = PickVoice(_loops, dspTime);
            var src = voice.src;
            src.Stop();
            src.clip = clip;
            src.loop = true;
            src.pitch = 1f;
            src.volume = 0f;
            src.panStereo = Mathf.Clamp(x / ShowData.MaxX, -1f, 1f) * panWidth;
            src.PlayScheduled(System.Math.Max(dspTime, AudioSettings.dspTime));
            voice.owner = owner;
            voice.start = dspTime;
            voice.busyUntil = double.MaxValue;
            voice.fadeFrom = 0f;
            voice.fadeStart = dspTime;
            voice.fadeEnd = dspTime + 0.25;
            voice.src.volume = 0f;
            _loopTarget[voice] = Mathf.Clamp01(volume * sfxVolume);
        }

        readonly Dictionary<Voice, float> _loopTarget = new Dictionary<Voice, float>();

        /// <summary>Fade out the loop owned by <paramref name="owner"/> starting at DSP time.</summary>
        public void StopLoop(int owner, double dspTime, float fade = 0.6f)
        {
            if (_loops == null) return;
            foreach (var v in _loops)
            {
                if (v.owner != owner) continue;
                v.fadeFrom = v.src.volume;
                v.fadeStart = dspTime;
                v.fadeEnd = dspTime + fade;
                _loopTarget[v] = 0f;
                v.owner = -2 - owner; // mark as stopping
            }
        }

        void Update()
        {
            if (_loops == null) return;
            double now = AudioSettings.dspTime;
            foreach (var v in _loops)
            {
                if (v.fadeStart < 0 || !_loopTarget.TryGetValue(v, out float target)) continue;
                if (now < v.fadeStart) continue;
                float u = (float)((now - v.fadeStart) / System.Math.Max(1e-3, v.fadeEnd - v.fadeStart));
                v.src.volume = Mathf.Lerp(v.fadeFrom, target, Mathf.Clamp01(u));
                if (u >= 1f)
                {
                    v.fadeStart = -1;
                    if (target <= 0f)
                    {
                        v.src.Stop();
                        v.busyUntil = 0;
                        v.owner = -1;
                    }
                }
            }
        }

        public void StopAll()
        {
            foreach (var kv in _voices)
                foreach (var v in kv.Value) { v.src.Stop(); v.busyUntil = 0; v.start = 0; }
            if (_loops != null)
                foreach (var v in _loops) { v.src.Stop(); v.busyUntil = 0; v.owner = -1; v.fadeStart = -1; }
            _loopTarget.Clear();
        }
    }
}
