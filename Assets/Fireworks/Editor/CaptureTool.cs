using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Fireworks.EditorTools
{
    /// <summary>
    /// Deterministic offline stills: particles are advanced with ParticleSystem.Simulate in fixed 1/60 s steps
    /// and the main camera is rendered into a RenderTexture. Output goes to &lt;project&gt;/Captures (git-ignored).
    /// Batch: -executeMethod Fireworks.EditorTools.CaptureTool.CaptureAll
    /// </summary>
    public static class CaptureTool
    {
        const int Width = 1920, Height = 1080;
        const float Dt = 1f / 60f;

        static string OutDir => Path.Combine(Directory.GetCurrentDirectory(), "Captures");

        struct TypeShot
        {
            public FireworkType type;
            public string palette;
            public float h;
            public float at; // song time of the still; the burst (or fountain start) is at 0
            public Vector3 camPos, look;
            public float fov;
        }

        static readonly Vector3 SkyCam = new Vector3(0f, 58f, -100f);
        static readonly Vector3 SkyLook = new Vector3(0f, 76f, 10f);

        static TypeShot[] TypeShots =>
            new[]
            {
                new TypeShot { type = FireworkType.Peony, palette = "red", h = 80, at = 1.0f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Chrysanthemum, palette = "cyan", h = 80, at = 1.15f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Willow, palette = "gold", h = 85, at = 2.6f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Ring, palette = "purple", h = 80, at = 0.9f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Crossette, palette = "green", h = 80, at = 1.3f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Strobe, palette = "silver", h = 80, at = 1.4f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Palm, palette = "gold", h = 80, at = 1.7f, camPos = SkyCam, look = SkyLook, fov = 58 },
                new TypeShot { type = FireworkType.Comet, palette = "silver", h = 90, at = -0.35f, camPos = new Vector3(0f, 40f, -120f), look = new Vector3(0f, 52f, 10f), fov = 58 },
                new TypeShot { type = FireworkType.Fountain, palette = "gold", h = 0, at = 2.5f, camPos = new Vector3(0f, 9f, -48f), look = new Vector3(0f, 8f, 10f), fov = 50 },
            };

        [MenuItem("Fireworks/Capture/All Stills", priority = 20)]
        public static void CaptureAll()
        {
            int code = 0;
            if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            try
            {
                Directory.CreateDirectory(OutDir);
                var report = new StringBuilder();
                CaptureTypes(report);
                CaptureShow(Builder.PreviewCues, "preview", new[] { -2.5f, 8.8f, 14.9f, 22.9f, 28.8f }, report);
                foreach (var sheet in new[] { "Assets/Fireworks/Show/cues_test.json", "Assets/Fireworks/Show/cues.json" })
                    if (File.Exists(sheet)) CaptureShow(sheet, Path.GetFileNameWithoutExtension(sheet), null, report);
                File.WriteAllText(Path.Combine(OutDir, "capture_report.txt"), report.ToString());
                Debug.Log("[Fireworks] Capture report\n" + report);
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                code = 1;
            }
            if (Application.isBatchMode) EditorApplication.Exit(code);
            else EditorSceneManager.OpenScene(SceneFactory.ScenePath, OpenSceneMode.Single); // drop capture-only changes
        }

        // ------------------------------------------------------------------ single types

        static void CaptureTypes(StringBuilder report)
        {
            EditorSceneManager.OpenScene(SceneFactory.ScenePath, OpenSceneMode.Single);
            var show = UnityEngine.Object.FindAnyObjectByType<ShowController>();
            var cam = Camera.main;
            var lib = show.library;
            var rt = MakeTarget(cam);
            ShaderUtil.allowAsyncCompilation = false;

            report.AppendLine("== single types (scale 1.0, camera ~110 m) ==");
            foreach (var shot in TypeShots)
            {
                var fw = UnityEngine.Object.Instantiate(lib.Prefab(shot.type));
                fw.name = "capture_" + shot.type;
                Seed(fw.gameObject, 4242 + (int)shot.type);
                var cue = new ShowCue { t = 0f, type = FireworkCatalog.Id(shot.type), x = 0f, z = 10f, h = shot.h, palette = shot.palette, scale = 1f, dur = 6f, Type = shot.type };
                bool aerial = FireworkCatalog.IsAerial(shot.type);
                double launch = aerial ? -FireworkCatalog.RiseTime(shot.type, shot.h) : 0.0;
                var origin = new Vector3(0f, 0.2f, 10f);
                var apex = new Vector3(0f, shot.h, 10f);
                fw.Launch(cue, 0, lib.Palette(shot.palette), launch, 0.0, aerial ? 0.0 : 6.0, origin, apex, new System.Random(9), null, true);
                double now = launch;
                int peak = 0;
                while (now < shot.at - 1e-6)
                {
                    now += Dt;
                    fw.Tick(now);
                    fw.SimulateStep(Dt);
                    peak = Math.Max(peak, fw.LiveParticles());
                }
                cam.transform.SetPositionAndRotation(shot.camPos, Quaternion.LookRotation(shot.look - shot.camPos));
                cam.fieldOfView = shot.fov;
                string file = $"type_{(int)shot.type}_{FireworkCatalog.Id(shot.type)}.png";
                Render(cam, rt, file);
                report.AppendLine($"{FireworkCatalog.Id(shot.type),-14} t={shot.at,5:0.00}s  live={fw.LiveParticles(),5}  peak={peak,5}  -> {file}");
                UnityEngine.Object.DestroyImmediate(fw.gameObject);
            }
            Release(cam, rt);
        }

        // ------------------------------------------------------------------ whole show

        static void CaptureShow(string sheetPath, string label, float[] stills, StringBuilder report)
        {
            EditorSceneManager.OpenScene(SceneFactory.ScenePath, OpenSceneMode.Single);
            var show = UnityEngine.Object.FindAnyObjectByType<ShowController>();
            var cam = Camera.main;
            var sheet = AssetDatabase.LoadAssetAtPath<TextAsset>(sheetPath);
            if (sheet == null)
            {
                report.AppendLine($"== {label}: sheet not found ({sheetPath}) ==");
                return;
            }
            show.cueSheet = sheet;
            show.music = null; // offline: internal clock

            // Render the overlay UI into the capture too (title and credit stills).
            var canvas = show.ui != null ? show.ui.GetComponent<Canvas>() : null;
            if (canvas != null)
            {
                canvas.renderMode = RenderMode.ScreenSpaceCamera;
                canvas.worldCamera = cam;
                canvas.planeDistance = 1f;
            }

            var rt = MakeTarget(cam);
            ShaderUtil.allowAsyncCompilation = false;
            UnityEngine.Random.InitState(77);
            show.Begin(true);
            foreach (var w in show.Warnings) report.AppendLine("  warning: " + w);

            var times = new List<float>();
            if (stills != null) times.AddRange(stills);
            else
            {
                // For an external sheet: one still per section, at its middle.
                foreach (var s in show.Data.sections) times.Add((s.start + s.end) * 0.5f);
            }
            times.Sort();

            var perSecond = new SortedDictionary<int, int>();
            int ti = 0;
            int steps = 0;
            bool creditShot = false;
            double creditAt = double.MaxValue;
            report.AppendLine($"== show '{label}' ({show.Data.cues.Length} cues, song {show.SongLength:0.0}s, lead-in {show.LeadIn:0.0}s) ==");
            while (steps < 60 * 60 * 15)
            {
                show.ManualStep(Dt);
                steps++;
                double now = show.SongTime;
                int sec = (int)Math.Floor(now);
                perSecond.TryGetValue(sec, out int prev);
                perSecond[sec] = Math.Max(prev, show.LiveParticles);

                while (ti < times.Count && now >= times[ti] - 1e-6)
                {
                    string file = $"show_{label}_{ti}_{times[ti]:00.0}s.png".Replace(',', '.');
                    Render(cam, rt, file);
                    report.AppendLine($"  still t={times[ti],6:0.00}s  live={show.LiveParticles,6}  fireworks={show.ActiveFireworks,3}  -> {file}");
                    ti++;
                }
                if (show.IsFinished && creditAt == double.MaxValue) creditAt = now + 3.0;
                if (now >= creditAt && !creditShot)
                {
                    string file = $"show_{label}_credit.png";
                    Render(cam, rt, file);
                    report.AppendLine($"  credit still at t={now:0.00}s -> {file}");
                    creditShot = true;
                    break;
                }
            }
            report.AppendLine($"  PEAK live particles {show.PeakParticles} at t={show.PeakParticlesTime:0.00}s; finished={show.IsFinished}; steps={steps}");
            var line = new StringBuilder("  per-second max: ");
            foreach (var kv in perSecond) if (kv.Key >= 0) line.Append($"{kv.Key}:{kv.Value} ");
            report.AppendLine(line.ToString());

            Release(cam, rt);
        }

        // ------------------------------------------------------------------ helpers

        static void Seed(GameObject go, int seed)
        {
            int i = 0;
            foreach (var ps in go.GetComponentsInChildren<ParticleSystem>(true))
            {
                ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
                ps.useAutoRandomSeed = false;
                ps.randomSeed = (uint)(seed * 31 + i++);
            }
        }

        static RenderTexture MakeTarget(Camera cam)
        {
            // HDR target: URP renders straight into an external target's format, so an 8-bit RT would clamp
            // before bloom/tonemapping. The game itself renders to the HDR intermediate buffer.
            var rt = new RenderTexture(Width, Height, 24, RenderTextureFormat.ARGBHalf, RenderTextureReadWrite.Linear);
            rt.Create();
            cam.targetTexture = rt;
            cam.aspect = Width / (float)Height;
            return rt;
        }

        static void Release(Camera cam, RenderTexture rt)
        {
            cam.targetTexture = null;
            rt.Release();
            UnityEngine.Object.DestroyImmediate(rt);
        }

        // A PNG that is open in a viewer can be locked (Win32 1224); retry, then fall back to a new name.
        static void WritePng(string file, byte[] bytes)
        {
            string path = Path.Combine(OutDir, file);
            for (int attempt = 0; attempt < 5; attempt++)
            {
                try
                {
                    File.WriteAllBytes(path, bytes);
                    return;
                }
                catch (IOException)
                {
                    System.Threading.Thread.Sleep(300);
                }
            }
            string alt = Path.Combine(OutDir, Path.GetFileNameWithoutExtension(file) + "_locked-retry.png");
            File.WriteAllBytes(alt, bytes);
            Debug.LogWarning($"[Fireworks] {file} was locked by another program; wrote {Path.GetFileName(alt)} instead");
        }

        static byte Enc(float linear) => (byte)Mathf.RoundToInt(Mathf.Clamp01(Mathf.LinearToGammaSpace(Mathf.Max(0f, linear))) * 255f);

        static void Render(Camera cam, RenderTexture rt, string file)
        {
            // Two renders: the first can trigger shader/pipeline warm-up.
            cam.Render();
            cam.Render();
            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            var hdr = new Texture2D(Width, Height, TextureFormat.RGBAHalf, false, true);
            hdr.ReadPixels(new Rect(0, 0, Width, Height), 0, 0);
            hdr.Apply();
            RenderTexture.active = prev;
            var px = hdr.GetPixels();
            var outPx = new Color32[px.Length];
            for (int i = 0; i < px.Length; i++)
            {
                var c = px[i];
                outPx[i] = new Color32(Enc(c.r), Enc(c.g), Enc(c.b), 255);
            }
            var tex = new Texture2D(Width, Height, TextureFormat.RGBA32, false);
            tex.SetPixels32(outPx);
            tex.Apply();
            WritePng(file, tex.EncodeToPNG());
            UnityEngine.Object.DestroyImmediate(tex);
            UnityEngine.Object.DestroyImmediate(hdr);
        }
    }
}
