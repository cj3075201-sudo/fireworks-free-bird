using System;
using System.Collections.Generic;
using UnityEngine;

namespace Fireworks
{
    /// <summary>
    /// Cue sheet written by the Python tools (Docs/설계.md, "시간표 파일").
    /// Field names must match the JSON exactly because JsonUtility is used.
    /// </summary>
    [Serializable]
    public class ShowData
    {
        public int version = 1;
        public string music = "";
        public string credit = "";
        public float duration;
        public float bpm;
        [Tooltip("Optional: song time of the big drop of a slow-burn song (0 or missing = none)")]
        public float drop;
        public ShowSection[] sections = new ShowSection[0];
        public ShowCue[] cues = new ShowCue[0];

        public const float MinX = -90f, MaxX = 90f;
        public const float MinZ = -10f, MaxZ = 30f;
        public const float MinH = 45f, MaxH = 120f;
        public const float MinScale = 0.6f, MaxScale = 1.6f;
        public const float DefaultFountainDur = 4f;

        /// <summary>Parse and sanitise a cue sheet. Out-of-range values are clamped, unknown types are dropped.</summary>
        public static ShowData Parse(string json, List<string> warnings)
        {
            ShowData data = null;
            try
            {
                data = JsonUtility.FromJson<ShowData>(json);
            }
            catch (Exception e)
            {
                warnings?.Add("Cue JSON parse failed: " + e.Message);
            }

            if (data == null) data = new ShowData();
            data.Sanitise(warnings);
            return data;
        }

        void Sanitise(List<string> warnings)
        {
            if (sections == null) sections = new ShowSection[0];
            if (cues == null) cues = new ShowCue[0];
            if (credit == null) credit = "";
            if (music == null) music = "";

            var kept = new List<ShowCue>(cues.Length);
            foreach (var c in cues)
            {
                if (c == null) continue;
                if (!FireworkCatalog.TryParse(c.type, out var type))
                {
                    warnings?.Add($"Unknown firework type '{c.type}' at t={c.t:0.00}, cue skipped");
                    continue;
                }
                c.Type = type;
                c.t = Mathf.Max(0f, c.t);
                c.x = Mathf.Clamp(c.x, MinX, MaxX);
                c.z = Mathf.Clamp(c.z, MinZ, MaxZ);
                c.scale = Mathf.Clamp(c.scale <= 0f ? 1f : c.scale, MinScale, MaxScale);
                if (type == FireworkType.Fountain)
                {
                    c.h = 0f;
                    c.dur = c.dur > 0f ? Mathf.Clamp(c.dur, 0.5f, 60f) : DefaultFountainDur;
                }
                else
                {
                    c.h = Mathf.Clamp(c.h, MinH, MaxH);
                }
                if (!Palettes.IsKnown(c.palette))
                {
                    warnings?.Add($"Unknown palette '{c.palette}' at t={c.t:0.00}, using gold");
                    c.palette = "gold";
                }
                kept.Add(c);
            }
            kept.Sort((a, b) => a.t.CompareTo(b.t));
            cues = kept.ToArray();

            var secs = new List<ShowSection>();
            foreach (var s in sections)
            {
                if (s == null) continue;
                if (s.end < s.start) { var tmp = s.end; s.end = s.start; s.start = tmp; }
                s.energy = Mathf.Clamp01(s.energy);
                s.name = string.IsNullOrEmpty(s.name) ? "verse" : s.name.Trim().ToLowerInvariant();
                secs.Add(s);
            }
            secs.Sort((a, b) => a.start.CompareTo(b.start));
            sections = secs.ToArray();

            if (drop < 0f || float.IsNaN(drop)) drop = 0f;
            float last = LastEventTime();
            if (drop > 0f && last > 0f && drop > last)
            {
                warnings?.Add($"drop {drop:0.00}s is after the last event ({last:0.00}s), ignored");
                drop = 0f;
            }
        }

        public bool HasDrop => drop > 0f;

        /// <summary>Last moment anything in the sheet is scheduled to happen (seconds, song time).</summary>
        public float LastEventTime()
        {
            float last = Mathf.Max(0f, duration);
            foreach (var c in cues)
                last = Mathf.Max(last, c.t + (c.Type == FireworkType.Fountain ? c.dur : 0f));
            foreach (var s in sections)
                last = Mathf.Max(last, s.end);
            return last;
        }
    }

    [Serializable]
    public class ShowSection
    {
        public string name;
        public float start;
        public float end;
        public float energy;
    }

    [Serializable]
    public class ShowCue
    {
        [Tooltip("Song time of the burst (aerial) or of the start (fountain), seconds")]
        public float t;
        public string type;
        public float x;
        public float z;
        public float h;
        public string palette = "gold";
        public float scale = 1f;
        public bool hit;
        public float dur;

        [NonSerialized] public FireworkType Type;
    }
}
