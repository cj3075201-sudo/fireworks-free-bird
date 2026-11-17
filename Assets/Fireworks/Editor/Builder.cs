using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Fireworks.EditorTools
{
    /// <summary>
    /// One-click regeneration of everything Unity-side.
    /// Menu: Fireworks/Build All. Batch: -executeMethod Fireworks.EditorTools.Builder.BuildAll
    /// </summary>
    public static class Builder
    {
        public const string LibraryPath = "Assets/Fireworks/Data/FireworkLibrary.asset";
        public const string BankPath = "Assets/Fireworks/Data/SfxBank.asset";
        public const string SfxDir = "Assets/Fireworks/Audio/SFX";
        public const string MusicDir = "Assets/Fireworks/Audio/Music";
        public const string ShowDir = "Assets/Fireworks/Show";
        public const string PreviewCues = "Assets/Fireworks/Dev/preview_cues.json";

        [MenuItem("Fireworks/Build All", priority = 0)]
        public static void BuildAll()
        {
            try
            {
                FxAssets.EnsureDir("Assets/Fireworks/Data");
                FxAssets.Build();
                var prefabs = FireworkFactory.BuildAll();

                var lib = LoadOrCreate<FireworkLibrary>(LibraryPath);
                lib.prefabs = prefabs;
                lib.palettes = Palettes.Defaults();
                EditorUtility.SetDirty(lib);

                var bank = LoadOrCreate<SfxBank>(BankPath);
                LinkSfx(bank);

                EnvironmentFactory.Build();
                SceneFactory.Build(lib, bank);
                CleanupTemplate();
                AssetDatabase.SaveAssets();
                Debug.Log($"[Fireworks] Build All done: {prefabs.Length} prefabs, {bank.ClipCount()} sfx clips linked, scene {SceneFactory.ScenePath}");
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                if (Application.isBatchMode) EditorApplication.Exit(1);
            }
        }

        /// <summary>Relink WAVs and the cue sheet without rebuilding prefabs (after the sound/cue tools ran).</summary>
        [MenuItem("Fireworks/Relink Audio and Cues", priority = 1)]
        public static void Relink()
        {
            var bank = LoadOrCreate<SfxBank>(BankPath);
            LinkSfx(bank);
            var scene = EditorSceneManager.OpenScene(SceneFactory.ScenePath, OpenSceneMode.Single);
            var show = UnityEngine.Object.FindAnyObjectByType<ShowController>();
            if (show != null)
            {
                if (show.sfx != null) show.sfx.bank = bank;
                LinkShowData(show);
                EditorUtility.SetDirty(show);
            }
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
            AssetDatabase.SaveAssets();
            Debug.Log($"[Fireworks] Relinked: {bank.ClipCount()} sfx clips, cues={(show != null && show.cueSheet != null ? show.cueSheet.name : "none")}, music={(show != null && show.music != null ? show.music.name : "none")}");
        }

        static T LoadOrCreate<T>(string path) where T : ScriptableObject
        {
            var a = AssetDatabase.LoadAssetAtPath<T>(path);
            if (a != null) return a;
            a = ScriptableObject.CreateInstance<T>();
            AssetDatabase.CreateAsset(a, path);
            return a;
        }

        public static void LinkSfx(SfxBank bank)
        {
            bank.boom = Clips(SfxBank.BoomNames);
            bank.pop = Clips(SfxBank.PopNames);
            bank.crackle = Clips(SfxBank.CrackleNames);
            bank.launch = Clips(SfxBank.LaunchNames);
            bank.whistle = Clips(SfxBank.WhistleNames);
            bank.fountainLoop = AssetDatabase.LoadAssetAtPath<AudioClip>($"{SfxDir}/{SfxBank.FountainLoopName}.wav");
            EditorUtility.SetDirty(bank);
        }

        static AudioClip[] Clips(string[] names)
        {
            var list = new System.Collections.Generic.List<AudioClip>();
            foreach (var n in names)
            {
                var c = AssetDatabase.LoadAssetAtPath<AudioClip>($"{SfxDir}/{n}.wav");
                if (c != null) list.Add(c);
            }
            return list.ToArray();
        }

        /// <summary>
        /// Default show wiring, best available first: Show/cues.json (real song) → Show/cues_test.json (test song)
        /// → Dev/preview_cues.json (no music, internal clock). The music file is the cue sheet's "music" field
        /// looked up in Audio/Music.
        /// </summary>
        public static void LinkShowData(ShowController show)
        {
            TextAsset sheet = null;
            AudioClip music = null;
            foreach (var name in new[] { "cues.json", "cues_test.json" })
            {
                var ta = AssetDatabase.LoadAssetAtPath<TextAsset>($"{ShowDir}/{name}");
                if (ta == null) continue;
                var clip = MusicFor(ta);
                if (name == "cues.json" && clip == null) continue; // real sheet without its song: fall through
                sheet = ta;
                music = clip;
                break;
            }
            if (sheet == null) sheet = AssetDatabase.LoadAssetAtPath<TextAsset>(PreviewCues);
            show.cueSheet = sheet;
            show.music = music;
            EditorUtility.SetDirty(show);
        }

        static AudioClip MusicFor(TextAsset sheet)
        {
            var data = ShowData.Parse(sheet.text, null);
            if (string.IsNullOrEmpty(data.music)) return null;
            string file = Path.GetFileName(data.music);
            return AssetDatabase.LoadAssetAtPath<AudioClip>($"{MusicDir}/{file}");
        }

        static void CleanupTemplate()
        {
            // Readme first: its script lives in TutorialInfo, and the asset cannot be loaded once that is gone.
            foreach (var p in new[] { "Assets/Readme.asset", "Assets/TutorialInfo", "Assets/Scenes/SampleScene.unity" })
                if (!string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(p, AssetPathToGUIDOptions.OnlyExistingAssets)))
                    AssetDatabase.DeleteAsset(p);
            if (AssetDatabase.IsValidFolder("Assets/Scenes") && AssetDatabase.FindAssets("", new[] { "Assets/Scenes" }).Length == 0)
                AssetDatabase.DeleteAsset("Assets/Scenes");
        }
    }
}
