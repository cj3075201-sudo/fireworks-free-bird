using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.UI;

namespace Fireworks.EditorTools
{
    /// <summary>Builds Assets/Fireworks/Scenes/Fireworks.unity from scratch.</summary>
    public static class SceneFactory
    {
        public const string ScenePath = "Assets/Fireworks/Scenes/Fireworks.unity";

        public static void Build(FireworkLibrary library, SfxBank bank)
        {
            FxAssets.EnsureDir("Assets/Fireworks/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            // Environment -------------------------------------------------------------
            var env = new GameObject("Environment");
            var night = env.AddComponent<NightEnvironment>();
            night.skybox = EnvironmentFactory.Sky;
            night.reflection = EnvironmentFactory.Reflection;
            night.Apply();
            AddMesh(env.transform, "Water", EnvironmentFactory.WaterMesh, EnvironmentFactory.Water);
            AddMesh(env.transform, "Embankment", EnvironmentFactory.PierMesh, EnvironmentFactory.Hills);
            AddMesh(env.transform, "City", EnvironmentFactory.CityMesh, EnvironmentFactory.City);
            AddMesh(env.transform, "Hills", EnvironmentFactory.HillMesh, EnvironmentFactory.Hills);

            var moon = new GameObject("Moonlight").AddComponent<Light>();
            moon.transform.SetParent(env.transform, false);
            moon.type = LightType.Directional;
            moon.color = new Color(0.55f, 0.65f, 1f);
            moon.intensity = 0.06f;
            moon.shadows = LightShadows.None;
            moon.transform.rotation = Quaternion.Euler(24f, 15f, 0f); // from behind the camera: rims the skyline, no glint on the water

            // Camera ------------------------------------------------------------------
            var camGo = new GameObject("Main Camera");
            camGo.tag = "MainCamera";
            var cam = camGo.AddComponent<Camera>();
            cam.allowHDR = true;
            cam.allowMSAA = false;
            cam.clearFlags = CameraClearFlags.Skybox;
            cam.nearClipPlane = 0.5f;
            cam.farClipPlane = 5000f;
            cam.fieldOfView = 50f;
            var camData = cam.GetUniversalAdditionalCameraData();
            camData.renderPostProcessing = true;
            camData.antialiasing = AntialiasingMode.FastApproximateAntialiasing;
            camData.dithering = true;
            camGo.AddComponent<AudioListener>();
            var director = camGo.AddComponent<ShowCamera>();
            director.cam = cam;
            var free = camGo.AddComponent<FreeCamera>();
            free.director = director;
            var intro = ShowCamera.DefaultShot;
            camGo.transform.SetPositionAndRotation(intro.posA, Quaternion.LookRotation(intro.lookA - intro.posA));

            // Post-processing -------------------------------------------------------
            var volGo = new GameObject("Global Volume");
            var vol = volGo.AddComponent<Volume>();
            vol.isGlobal = true;
            vol.priority = 1f;
            vol.sharedProfile = AssetDatabase.LoadAssetAtPath<VolumeProfile>(EnvironmentFactory.VolumePath);
            director.volume = vol;

            // UI ----------------------------------------------------------------------
            var ui = BuildUI();

            // Show ----------------------------------------------------------------------
            var showGo = new GameObject("Show");
            var show = showGo.AddComponent<ShowController>();
            var music = showGo.AddComponent<AudioSource>();
            music.playOnAwake = false;
            music.spatialBlend = 0f;
            music.priority = 0;
            show.musicSource = music;
            show.library = library;
            show.director = director;
            show.ui = ui;

            var sfxGo = new GameObject("Sfx");
            sfxGo.transform.SetParent(showGo.transform, false);
            var sfx = sfxGo.AddComponent<ShowAudio>();
            sfx.bank = bank;
            show.sfx = sfx;

            var lightsGo = new GameObject("Burst Lights");
            lightsGo.transform.SetParent(showGo.transform, false);
            show.burstLights = lightsGo.AddComponent<BurstLights>();

            Builder.LinkShowData(show);

            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
        }

        static void AddMesh(Transform parent, string name, Mesh mesh, Material mat)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var r = go.AddComponent<MeshRenderer>();
            r.sharedMaterial = mat;
            r.shadowCastingMode = ShadowCastingMode.Off;
            r.receiveShadows = false;
            r.lightProbeUsage = LightProbeUsage.Off;
            GameObjectUtility.SetStaticEditorFlags(go, StaticEditorFlags.BatchingStatic);
        }

        // ------------------------------------------------------------------ UI

        static Font UIFont => Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");

        static ShowUI BuildUI()
        {
            var go = new GameObject("UI");
            var canvas = go.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 10;
            var scaler = go.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920f, 1080f);
            scaler.matchWidthOrHeight = 0.5f;
            var ui = go.AddComponent<ShowUI>();

            ui.flash = FullImage(go.transform, "Flash", new Color(1f, 1f, 1f, 0f));
            ui.flash.enabled = false;
            ui.fader = FullImage(go.transform, "Fader", Color.black);

            var title = Group(go.transform, "Title");
            ui.titleGroup = title;
            ui.titleText = Label(title.transform, "Title Text", "F I R E W O R K S", 88, new Color(1f, 0.94f, 0.86f), new Vector2(0f, 30f));
            ui.subtitleText = Label(title.transform, "Subtitle", "a particle show", 28, new Color(0.72f, 0.73f, 0.82f), new Vector2(0f, -50f));
            title.alpha = 0f;

            var credit = Group(go.transform, "Credit");
            ui.creditGroup = credit;
            ui.creditText = Label(credit.transform, "Credit Text", "", 32, new Color(0.92f, 0.9f, 0.88f), new Vector2(0f, 0f));
            ui.hintText = Label(credit.transform, "Hint", "R  replay      ESC  quit", 20, new Color(0.55f, 0.56f, 0.62f), new Vector2(0f, -70f));
            credit.alpha = 0f;
            return ui;
        }

        static Image FullImage(Transform parent, string name, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            var rt = (RectTransform)go.transform;
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            var img = go.AddComponent<Image>();
            img.color = color;
            img.raycastTarget = false;
            return img;
        }

        static CanvasGroup Group(Transform parent, string name)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            var rt = (RectTransform)go.transform;
            rt.anchorMin = Vector2.zero;
            rt.anchorMax = Vector2.one;
            rt.offsetMin = Vector2.zero;
            rt.offsetMax = Vector2.zero;
            var g = go.AddComponent<CanvasGroup>();
            g.interactable = false;
            g.blocksRaycasts = false;
            return g;
        }

        static Text Label(Transform parent, string name, string text, int size, Color color, Vector2 pos)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            var rt = (RectTransform)go.transform;
            rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0.5f);
            rt.sizeDelta = new Vector2(1600f, size * 1.6f);
            rt.anchoredPosition = pos;
            var t = go.AddComponent<Text>();
            t.font = UIFont;
            t.text = text;
            t.fontSize = size;
            t.color = color;
            t.alignment = TextAnchor.MiddleCenter;
            t.horizontalOverflow = HorizontalWrapMode.Overflow;
            t.verticalOverflow = VerticalWrapMode.Overflow;
            t.raycastTarget = false;
            return t;
        }
    }
}
