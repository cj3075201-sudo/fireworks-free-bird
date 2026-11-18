using System.IO;
using UnityEngine;
using UnityEngine.InputSystem;

namespace Fireworks
{
    /// <summary>
    /// Live volume panel, toggled with V during Play. Moves the music and the per-kind firework gains;
    /// sounds already playing follow at once (ShowAudio re-applies gains every frame).
    /// In the editor every change is saved to Assets/Fireworks/Resources/mix.json. That file is loaded at
    /// startup (read from disk in the editor, as a Resources TextAsset in a player build), so the tuned
    /// mix travels with the repository and the build. ShowAudio adds this component at runtime.
    /// </summary>
    public class MixPanel : MonoBehaviour
    {
        public const string RelativePath = "Fireworks/Resources/mix.json";
        const string ResourceName = "mix";
        const float MuteDb = -30f;     // slider bottom = off
        const float MaxBoostDb = 12f;

        [System.Serializable]
        public class MixSettings
        {
            public float music = 0.85f;
            public float sfx = 1f;
            public float boom, pop, crackle, launch, whistle, loop;
            public float hitAccent = 1.6f;
        }

        ShowAudio _audio;
        ShowController _show;
        MasterLimiter _limiter;
        MixSettings _defaults; // code/scene values, the "0 dB" reference
        bool _open;
        bool _dirty;
        float _dirtyAt;
        string _status = "";
        float _limHold, _limHoldUntil;
        float _hintUntil;

        GUIStyle _panel, _title, _label, _value, _small, _button;
        Texture2D _bg;

        static string FilePath => Path.Combine(Application.dataPath, RelativePath);

        void Awake()
        {
            _audio = GetComponent<ShowAudio>();
            _show = FindAnyObjectByType<ShowController>();
            _defaults = Capture();
            Load();
            _hintUntil = Time.unscaledTime + 8f;
        }

        void Update()
        {
            var kb = Keyboard.current;
            if (kb != null && kb.vKey.wasPressedThisFrame) _open = !_open;
            if (_dirty && Time.unscaledTime - _dirtyAt > 0.4f) Save();
        }

        void OnDisable()
        {
            if (_dirty) Save();
        }

        // ------------------------------------------------------------------ values

        MixSettings Capture()
        {
            var m = new MixSettings();
            if (_show != null) m.music = _show.musicVolume;
            if (_audio != null)
            {
                m.sfx = _audio.sfxVolume;
                m.boom = _audio.boomGain; m.pop = _audio.popGain; m.crackle = _audio.crackleGain;
                m.launch = _audio.launchGain; m.whistle = _audio.whistleGain; m.loop = _audio.loopGain;
                m.hitAccent = _audio.hitAccent;
            }
            return m;
        }

        void Apply(MixSettings m)
        {
            if (_audio != null)
            {
                _audio.sfxVolume = Mathf.Clamp(m.sfx, 0f, 4f);
                _audio.boomGain = Mathf.Clamp01(m.boom);
                _audio.popGain = Mathf.Clamp01(m.pop);
                _audio.crackleGain = Mathf.Clamp01(m.crackle);
                _audio.launchGain = Mathf.Clamp01(m.launch);
                _audio.whistleGain = Mathf.Clamp01(m.whistle);
                _audio.loopGain = Mathf.Clamp01(m.loop);
                _audio.hitAccent = Mathf.Clamp(m.hitAccent, 1f, 3f);
            }
            if (_show != null)
            {
                _show.musicVolume = Mathf.Clamp01(m.music);
                if (_show.musicSource != null) _show.musicSource.volume = _show.musicVolume;
            }
        }

        void Load()
        {
            try
            {
                string json = null;
#if UNITY_EDITOR
                // Straight from disk: the imported TextAsset can lag behind a save made during Play.
                if (File.Exists(FilePath)) json = File.ReadAllText(FilePath);
#else
                var asset = Resources.Load<TextAsset>(ResourceName);
                if (asset != null) json = asset.text;
#endif
                if (string.IsNullOrEmpty(json)) return;
                var m = JsonUtility.FromJson<MixSettings>(json);
                if (m == null) return;
                Apply(m);
                _status = "mix.json 값을 불러왔어요";
            }
            catch (System.Exception e)
            {
                Debug.LogWarning($"[MixPanel] mix.json 읽기 실패, 기본값 사용: {e.Message}");
            }
        }

        void Save()
        {
            _dirty = false;
#if UNITY_EDITOR
            try
            {
                File.WriteAllText(FilePath, JsonUtility.ToJson(Capture(), true));
                _status = "자동 저장됨 · Assets/" + RelativePath;
            }
            catch (System.Exception e)
            {
                _status = "저장 실패: " + e.Message;
            }
#endif
        }

        void MarkDirty()
        {
            _dirty = true;
            _dirtyAt = Time.unscaledTime;
        }

        // ------------------------------------------------------------------ UI

        void OnGUI()
        {
            if (_audio == null) return;
            EnsureStyles();
            float scale = Mathf.Clamp(Screen.height / 900f, 0.8f, 2f);
            var keep = GUI.matrix;
            GUI.matrix = Matrix4x4.Scale(new Vector3(scale, scale, 1f));
            float w = Screen.width / scale, h = Screen.height / scale;

            if (!_open)
            {
                if (Time.unscaledTime < _hintUntil)
                    GUI.Label(new Rect(w - 196f, h - 34f, 180f, 22f), "V  볼륨 조절", _small);
                GUI.matrix = keep;
                return;
            }

            const float pw = 400f;
            GUILayout.BeginArea(new Rect(w - pw - 16f, 16f, pw, h - 32f));
            GUILayout.BeginVertical(_panel);
            GUILayout.Label("볼륨 조절", _title);
            GUILayout.Label("0 dB = 지금 기본값 · 바꾸면 바로 들리고 자동 저장", _small);
            GUILayout.Space(8f);

            var m = Capture();
            bool changed = false;
            changed |= DbRow("음악", ref m.music, _defaults.music, 20f * Mathf.Log10(1f / Mathf.Max(1e-4f, _defaults.music)), 1f);
            GUILayout.Space(4f);
            changed |= DbRow("폭죽 전체", ref m.sfx, _defaults.sfx, MaxBoostDb, 4f);
            GUILayout.Space(4f);
            changed |= DbRow("  큰 폭발", ref m.boom, _defaults.boom, MaxBoostDb, 1f);
            changed |= DbRow("  작은 폭발", ref m.pop, _defaults.pop, MaxBoostDb, 1f);
            changed |= DbRow("  발사음", ref m.launch, _defaults.launch, MaxBoostDb, 1f);
            changed |= DbRow("  지글지글", ref m.crackle, _defaults.crackle, MaxBoostDb, 1f);
            changed |= DbRow("  휘파람", ref m.whistle, _defaults.whistle, MaxBoostDb, 1f);
            changed |= DbRow("  분수", ref m.loop, _defaults.loop, MaxBoostDb, 1f);
            GUILayout.Space(4f);
            changed |= AccentRow(ref m.hitAccent);

            if (changed)
            {
                Apply(m);
                MarkDirty();
            }

            GUILayout.Space(10f);
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("처음 값으로", _button, GUILayout.Width(130f)))
            {
                Apply(_defaults);
                MarkDirty();
            }
            GUILayout.FlexibleSpace();
            if (GUILayout.Button("닫기 (V)", _button, GUILayout.Width(100f))) _open = false;
            GUILayout.EndHorizontal();

            GUILayout.Space(8f);
            LimiterRow();
            if (!string.IsNullOrEmpty(_status)) GUILayout.Label(_status, _small);
            GUILayout.EndVertical();
            GUILayout.EndArea();
            GUI.matrix = keep;
        }

        /// <summary>One slider in dB relative to the default; the bottom of the travel mutes.</summary>
        bool DbRow(string name, ref float value, float reference, float maxDb, float maxLinear)
        {
            float db = value <= 0f || reference <= 0f ? MuteDb : 20f * Mathf.Log10(value / reference);
            db = Mathf.Clamp(db, MuteDb, maxDb);
            GUILayout.BeginHorizontal();
            GUILayout.Label(name, _label, GUILayout.Width(96f));
            float next = GUILayout.HorizontalSlider(db, MuteDb, maxDb, GUILayout.Width(190f));
            GUILayout.Label(next <= MuteDb + 0.05f ? "끔" : $"{next:+0.0;-0.0;0.0} dB", _value, GUILayout.Width(70f));
            GUILayout.EndHorizontal();
            if (Mathf.Abs(next - db) < 0.01f) return false;
            next = Mathf.Round(next * 2f) / 2f; // 0.5 dB steps
            value = next <= MuteDb + 0.05f ? 0f : Mathf.Min(maxLinear, reference * Mathf.Pow(10f, next / 20f));
            return true;
        }

        bool AccentRow(ref float accent)
        {
            GUILayout.BeginHorizontal();
            GUILayout.Label("큰 박자 강조", _label, GUILayout.Width(96f));
            float next = GUILayout.HorizontalSlider(accent, 1f, 3f, GUILayout.Width(190f));
            GUILayout.Label($"×{next:0.0}", _value, GUILayout.Width(70f));
            GUILayout.EndHorizontal();
            if (Mathf.Abs(next - accent) < 0.001f) return false;
            accent = Mathf.Round(next * 10f) / 10f;
            return true;
        }

        void LimiterRow()
        {
            if (_limiter == null) _limiter = FindAnyObjectByType<MasterLimiter>();
            if (_limiter == null) return;
            float now = _limiter.ReductionDb;
            if (now >= _limHold || Time.unscaledTime > _limHoldUntil)
            {
                _limHold = now;
                _limHoldUntil = Time.unscaledTime + 0.6f;
            }
            var c = _limHold < 1f ? new Color(0.75f, 0.9f, 0.75f) : _limHold < 4f ? new Color(1f, 0.85f, 0.4f) : new Color(1f, 0.45f, 0.4f);
            var keep = _small.normal.textColor;
            _small.normal.textColor = c;
            GUILayout.Label(_limHold < 0.1f
                ? "소리 깨짐 방지: 쉬는 중"
                : $"소리 깨짐 방지: -{_limHold:0.0} dB 누르는 중{(_limHold >= 4f ? " · 너무 큼" : "")}", _small);
            _small.normal.textColor = keep;
        }

        void EnsureStyles()
        {
            if (_panel != null) return;
            _bg = new Texture2D(1, 1) { hideFlags = HideFlags.HideAndDontSave };
            _bg.SetPixel(0, 0, new Color(0.04f, 0.05f, 0.08f, 0.82f));
            _bg.Apply();
            _panel = new GUIStyle(GUI.skin.box) { padding = new RectOffset(16, 16, 12, 14) };
            _panel.normal.background = _bg;
            _title = new GUIStyle(GUI.skin.label) { fontSize = 17, fontStyle = FontStyle.Bold };
            _title.normal.textColor = Color.white;
            _label = new GUIStyle(GUI.skin.label) { fontSize = 14 };
            _label.normal.textColor = new Color(0.92f, 0.92f, 0.95f);
            _value = new GUIStyle(_label) { alignment = TextAnchor.MiddleRight };
            _small = new GUIStyle(GUI.skin.label) { fontSize = 12, wordWrap = true };
            _small.normal.textColor = new Color(0.7f, 0.72f, 0.78f);
            _button = new GUIStyle(GUI.skin.button) { fontSize = 13 };
        }

        void OnDestroy()
        {
            if (_bg != null) Destroy(_bg);
        }
    }
}
