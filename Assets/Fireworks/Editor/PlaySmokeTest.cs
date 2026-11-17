using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Fireworks.EditorTools
{
    /// <summary>
    /// Runs the real scene in Play mode for the whole song and logs clock, music, particle counts and any errors.
    /// Batch (no -quit; it exits by itself): -executeMethod Fireworks.EditorTools.PlaySmokeTest.Run
    /// </summary>
    [InitializeOnLoad]
    public static class PlaySmokeTest
    {
        const string Flag = "Fireworks.PlaySmokeTest";
        static double _startReal = -1, _nextLog, _finishedAt = -1;
        static int _errors, _warnings;
        static readonly StringBuilder Log = new StringBuilder();

        static PlaySmokeTest()
        {
            if (!SessionState.GetBool(Flag, false)) return;
            EditorApplication.update += Tick;
            Application.logMessageReceived += OnLog;
        }

        [MenuItem("Fireworks/Play Smoke Test", priority = 30)]
        public static void Run()
        {
            EditorSceneManager.OpenScene(SceneFactory.ScenePath, OpenSceneMode.Single);
            SessionState.SetBool(Flag, true);
            EditorApplication.EnterPlaymode();
        }

        static void OnLog(string msg, string stack, LogType type)
        {
            if (msg.StartsWith("[Smoke]")) return;
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert)
            {
                _errors++;
                Log.AppendLine($"  ERROR: {msg}");
            }
            else if (type == LogType.Warning)
            {
                _warnings++;
                Log.AppendLine($"  warning: {msg}");
            }
        }

        static void Tick()
        {
            if (!EditorApplication.isPlaying) return;
            var show = Object.FindAnyObjectByType<ShowController>();
            if (show == null) return;
            double real = EditorApplication.timeSinceStartup;
            if (_startReal < 0)
            {
                _startReal = real;
                var bank = show.sfx != null ? show.sfx.bank : null;
                Debug.Log($"[Smoke] start: cues={(show.cueSheet ? show.cueSheet.name : "none")} music={(show.music ? show.music.name : "none")} " +
                          $"sfxClips={(bank ? bank.ClipCount() : 0)} cueCount={show.Data?.cues.Length} leadIn={show.LeadIn:0.00}s end={show.EndTime:0.0}s");
            }
            double el = real - _startReal;
            var music = show.musicSource;
            if (real >= _nextLog)
            {
                _nextLog = real + 5.0;
                Debug.Log($"[Smoke] real={el,6:0.0}s song={show.SongTime,7:0.00}s musicPlaying={(music && music.isPlaying)} musicTime={(music ? music.time : 0f),6:0.00} " +
                          $"dsp={AudioSettings.dspTime:0.00} fireworks={show.ActiveFireworks,3} live={show.LiveParticles,6} peak={show.PeakParticles}");
            }
            if (show.IsFinished && _finishedAt < 0) _finishedAt = real;
            bool done = _finishedAt > 0 && real - _finishedAt > 3.0;
            bool timeout = el > show.EndTime + show.LeadIn + 45.0;
            if (!done && !timeout) return;

            Debug.Log($"[Smoke] {(done ? "FINISHED" : "TIMEOUT")} after {el:0.0}s real; song={show.SongTime:0.00}s " +
                      $"peak={show.PeakParticles} at {show.PeakParticlesTime:0.00}s; errors={_errors} warnings={_warnings}\n{Log}");
            SessionState.SetBool(Flag, false);
            EditorApplication.update -= Tick;
            EditorApplication.Exit(done && _errors == 0 ? 0 : 1);
        }
    }
}
