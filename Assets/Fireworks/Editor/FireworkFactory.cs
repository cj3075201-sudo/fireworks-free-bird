using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using MinMax = UnityEngine.ParticleSystem.MinMaxCurve;

namespace Fireworks.EditorTools
{
    /// <summary>
    /// Builds the nine firework prefabs with the ParticleSystem API.
    /// Structure: Head (rising shell, moved by script) → Burst (stars, sub-emitters, glow, flash, smoke).
    /// Units are metres and seconds; the camera sits ~150–220 m away.
    /// </summary>
    public static class FireworkFactory
    {
        public const string PrefabDir = "Assets/Fireworks/Prefabs";

        public static Firework[] BuildAll()
        {
            FxAssets.EnsureDir(PrefabDir);
            var result = new Firework[FireworkCatalog.TypeCount];
            for (int i = 0; i < FireworkCatalog.TypeCount; i++)
            {
                var type = (FireworkType)i;
                var root = new GameObject("FW_" + FireworkCatalog.Id(type));
                try
                {
                    Build(type, root);
                    string path = $"{PrefabDir}/FW_{FireworkCatalog.Id(type)}.prefab";
                    var prefab = PrefabUtility.SaveAsPrefabAsset(root, path);
                    result[i] = prefab.GetComponent<Firework>();
                }
                finally
                {
                    Object.DestroyImmediate(root);
                }
            }
            return result;
        }

        // ------------------------------------------------------------------ per type

        static void Build(FireworkType type, GameObject root)
        {
            var fw = root.AddComponent<Firework>();
            fw.type = type;
            var parts = new List<FxPart>();
            var head = new GameObject("Head").transform;
            head.SetParent(root.transform, false);
            var burst = new GameObject("Burst").transform;
            burst.SetParent(root.transform, false);
            fw.head = head;
            fw.burstRoot = burst;

            var headSys = new List<ParticleSystem>();
            var burstSys = new List<ParticleSystem>();

            if (type != FireworkType.Fountain)
            {
                bool comet = type == FireworkType.Comet;
                bool palm = type == FireworkType.Palm;
                var sparks = HeadSparks(head, comet ? 300 : palm ? 150 : 70, comet || palm);
                headSys.Add(sparks);
                parts.Add(Part(sparks, comet ? ColorRole.Primary : ColorRole.Fixed, comet ? 0.35f : 0f, false, false, 0f));
                var core = HeadCore(head, comet ? 2.6f : palm ? 1.5f : 1.0f);
                headSys.Add(core);
                fw.headCore = core;
                parts.Add(Part(core, comet ? ColorRole.Primary : ColorRole.Fixed, 0.6f, false, false, 0f));
                if (comet)
                {
                    var ribbon = HeadRibbon(head);
                    headSys.Add(ribbon);
                    parts.Add(Part(ribbon, ColorRole.Primary, 0.25f, false, false, 0f));
                }
            }

            switch (type)
            {
                case FireworkType.Peony:
                {
                    var stars = StarBurst("Stars", burst, FxAssets.Star, 260, 33f, 36f, 2.1f, 2.6f, 1.1f, 1.5f, 0.045f, 0.2f);
                    ColorShift(stars, 0.7f);
                    Shrink(stars, 0.6f);
                    burstSys.Add(stars);
                    parts.Add(Part(stars, ColorRole.PrimaryToSecondary, 0.08f, true, true, 0.4f));
                    fw.lightStrength = 1f;
                    break;
                }
                case FireworkType.Chrysanthemum:
                {
                    var stars = StarBurst("Stars", burst, FxAssets.Star, 200, 31f, 34f, 2.2f, 2.6f, 0.75f, 0.95f, 0.04f, 0.22f);
                    Trails(stars, 0.32f, 0.75f);
                    burstSys.Add(stars);
                    parts.Add(Part(stars, ColorRole.Primary, 0.12f, true, true, 0.4f));
                    var pistil = StarBurst("Pistil", burst, FxAssets.Star, 55, 10f, 12f, 1.5f, 1.8f, 0.8f, 1.0f, 0.06f, 0.15f);
                    burstSys.Add(pistil);
                    parts.Add(Part(pistil, ColorRole.Secondary, 0.25f, true, true, 0.4f));
                    fw.lightStrength = 1f;
                    break;
                }
                case FireworkType.Willow:
                {
                    var stars = StarBurst("Stars", burst, FxAssets.Star, 110, 30f, 33f, 5.0f, 6.0f, 0.7f, 0.9f, 0.06f, 0.28f);
                    Trails(stars, 0.42f, 0.7f, golden: true);
                    FadeOut(stars, 0.55f);
                    burstSys.Add(stars);
                    parts.Add(Part(stars, ColorRole.WarmTint, 0f, true, true, 0.4f));
                    var glitter = Glitter(stars, 5f, new Color(1f, 0.62f, 0.22f));
                    parts.Add(Part(glitter, ColorRole.Fixed, 0f, false, false, 0f));
                    fw.lightStrength = 0.75f;
                    break;
                }
                case FireworkType.Ring:
                {
                    var ring = Ring("Ring", burst, 90, 33f, 1.9f, 2.2f);
                    burstSys.Add(ring);
                    parts.Add(Part(ring, ColorRole.Primary, 0.1f, true, true, 0.4f));
                    var core = StarBurst("Core", burst, FxAssets.Star, 56, 10f, 11f, 1.3f, 1.6f, 0.7f, 0.9f, 0.06f, 0.15f);
                    burstSys.Add(core);
                    parts.Add(Part(core, ColorRole.Secondary, 0.3f, true, true, 0.4f));
                    fw.tiltRange = new Vector2(35f, 30f);
                    fw.randomRoll = true;
                    fw.lightStrength = 0.6f;
                    break;
                }
                case FireworkType.Crossette:
                {
                    var stars = StarBurst("Stars", burst, FxAssets.Star, 14, 18f, 20f, 1.0f, 1.0f, 1.3f, 1.5f, 0.04f, 0.2f);
                    FadeOut(stars, 0.98f);
                    Trails(stars, 0.35f, 0.7f);
                    burstSys.Add(stars);
                    parts.Add(Part(stars, ColorRole.Primary, 0.15f, true, true, 0.4f));
                    var split = Split(stars);
                    parts.Add(Part(split, ColorRole.Secondary, 0.2f, true, false, 0.3f));
                    var pop = SplitFlash(stars);
                    parts.Add(Part(pop, ColorRole.Secondary, 0.6f, false, false, 0f));
                    fw.randomRoll = true;
                    fw.secondaryDelay = 1.0f;
                    fw.lightStrength = 0.7f;
                    break;
                }
                case FireworkType.Strobe:
                {
                    var stars = StarBurst("Stars", burst, FxAssets.Strobe, 180, 12f, 26f, 2.6f, 3.4f, 0.9f, 1.2f, 0.06f, 0.12f);
                    var sh = stars.shape;
                    sh.radiusThickness = 1f; // filled sphere: strobes hang as a cloud, not a shell
                    Blink(stars, 18f);
                    FadeOut(stars, 0.8f);
                    burstSys.Add(stars);
                    parts.Add(Part(stars, ColorRole.Primary, 0.55f, true, true, 0.4f));
                    fw.lightStrength = 0.5f;
                    break;
                }
                case FireworkType.Palm:
                {
                    var fronds = StarBurst("Fronds", burst, FxAssets.Star, 9, 34f, 37f, 2.6f, 3.0f, 2.2f, 2.6f, 0.018f, 1.0f);
                    var sh = fronds.shape;
                    sh.shapeType = ParticleSystemShapeType.Cone;
                    sh.angle = 55f;
                    sh.radius = 0.3f;
                    sh.radiusThickness = 0f;
                    sh.rotation = new Vector3(-90f, 0f, 0f); // cone opens upward
                    sh.arcMode = ParticleSystemShapeMultiModeValue.BurstSpread;
                    Trails(fronds, 0.6f, 1.0f, golden: true);
                    burstSys.Add(fronds);
                    parts.Add(Part(fronds, ColorRole.Primary, 0.35f, true, true, 0.3f));
                    var glitter = Glitter(fronds, 32f, new Color(1f, 0.7f, 0.3f));
                    parts.Add(Part(glitter, ColorRole.WarmTint, 0f, false, false, 0f));
                    fw.lightStrength = 1.1f;
                    break;
                }
                case FireworkType.Comet:
                {
                    var fizzle = StarBurst("Fizzle", burst, FxAssets.Glitter, 60, 4f, 9f, 0.5f, 1.1f, 0.35f, 0.55f, 0.1f, 0.3f);
                    var sh = fizzle.shape;
                    sh.radiusThickness = 1f;
                    Blink(fizzle, 14f);
                    burstSys.Add(fizzle);
                    parts.Add(Part(fizzle, ColorRole.Primary, 0.4f, false, true, 0f));
                    fw.lightStrength = 0.2f;
                    break;
                }
                case FireworkType.Fountain:
                {
                    var spray = FountainSpray(burst);
                    burstSys.Add(spray);
                    parts.Add(Part(spray, ColorRole.Primary, 0.3f, true, true, 0.3f));
                    var baseGlow = FountainGlow(burst);
                    burstSys.Add(baseGlow);
                    parts.Add(Part(baseGlow, ColorRole.Primary, 0.2f, false, false, 1f));
                    var smoke = FountainSmoke(burst);
                    burstSys.Add(smoke);
                    parts.Add(Part(smoke, ColorRole.Fixed, 0f, false, false, 1f));
                    var lightGo = new GameObject("SustainLight");
                    lightGo.transform.SetParent(burst, false);
                    lightGo.transform.localPosition = new Vector3(0f, 5f, 0f);
                    var l = lightGo.AddComponent<Light>();
                    l.type = LightType.Point;
                    l.range = 140f;
                    l.shadows = LightShadows.None;
                    l.enabled = false;
                    fw.sustainLight = l;
                    fw.sustainIntensity = 260f;
                    fw.lightStrength = 0f; // the sustain light replaces the one-off burst flash
                    break;
                }
            }

            if (type != FireworkType.Fountain && type != FireworkType.Comet)
            {
                var glow = Glow(burst, type == FireworkType.Willow ? 46f : 56f);
                burstSys.Add(glow);
                parts.Add(Part(glow, type == FireworkType.Willow ? ColorRole.WarmTint : ColorRole.Primary, 0.15f, false, false, 1f));
                var flash = Flash(burst);
                burstSys.Add(flash);
                parts.Add(Part(flash, ColorRole.Primary, 0.6f, false, false, 0.5f));
                var smoke = Smoke(burst);
                burstSys.Add(smoke);
                parts.Add(Part(smoke, ColorRole.Fixed, 0f, false, false, 1f));
            }

            fw.headSystems = headSys.ToArray();
            fw.burstSystems = burstSys.ToArray();
            fw.parts = parts.ToArray();
        }

        static FxPart Part(ParticleSystem ps, ColorRole role, float white, bool speed, bool count, float sizeExp)
        {
            return new FxPart { system = ps, role = role, whiteMix = white, scaleSpeed = speed, scaleCount = count, sizeExponent = sizeExp };
        }

        // ------------------------------------------------------------------ building blocks

        static ParticleSystem Make(string name, Transform parent, Material mat, int maxParticles, bool loop = false,
            ParticleSystemSimulationSpace space = ParticleSystemSimulationSpace.World)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            var ps = go.AddComponent<ParticleSystem>();
            ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
            var main = ps.main;
            main.playOnAwake = false;
            main.loop = loop;
            main.duration = 1f;
            main.simulationSpace = space;
            main.scalingMode = ParticleSystemScalingMode.Hierarchy;
            main.maxParticles = maxParticles;
            main.startColor = Color.white;
            main.startRotation = 0f;
            main.gravityModifier = 0f;
            main.startSpeed = 0f;
            main.cullingMode = ParticleSystemCullingMode.AlwaysSimulate;
            main.stopAction = ParticleSystemStopAction.None;
            var em = ps.emission;
            em.enabled = true;
            em.rateOverTime = 0f;
            em.rateOverDistance = 0f;
            em.SetBursts(new ParticleSystem.Burst[0]);
            var sh = ps.shape;
            sh.enabled = false;
            var r = go.GetComponent<ParticleSystemRenderer>();
            r.sharedMaterial = mat;
            r.renderMode = ParticleSystemRenderMode.Billboard;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows = false;
            r.lightProbeUsage = UnityEngine.Rendering.LightProbeUsage.Off;
            r.reflectionProbeUsage = UnityEngine.Rendering.ReflectionProbeUsage.Off;
            r.minParticleSize = 0.0012f; // keep far sparks at least ~1 px so they do not shimmer away
            r.maxParticleSize = 0.5f;
            return ps;
        }

        static ParticleSystem StarBurst(string name, Transform parent, Material mat, int count,
            float speedMin, float speedMax, float lifeMin, float lifeMax, float sizeMin, float sizeMax, float drag, float gravity)
        {
            var ps = Make(name, parent, mat, Mathf.CeilToInt(count * 1.7f) + 16);
            var main = ps.main;
            main.startSpeed = new MinMax(speedMin, speedMax);
            main.startLifetime = Mathf.Approximately(lifeMin, lifeMax) ? new MinMax(lifeMin) : new MinMax(lifeMin, lifeMax);
            main.startSize = new MinMax(sizeMin, sizeMax);
            main.gravityModifier = gravity;
            var em = ps.emission;
            em.SetBursts(new[] { new ParticleSystem.Burst(0f, new MinMax(count)) });
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Sphere;
            sh.radius = 0.4f;
            sh.radiusThickness = 0f;
            Drag(ps, drag);
            FadeOut(ps, 0.7f);
            return ps;
        }

        /// <summary>
        /// Quadratic air drag: deceleration = c·v² (Unity drag with "multiply by velocity").
        /// Travel after t seconds is ln(1 + c·v0·t)/c; terminal fall speed is sqrt(g·gravityModifier / c).
        /// </summary>
        static void Drag(ParticleSystem ps, float c)
        {
            var lv = ps.limitVelocityOverLifetime;
            lv.enabled = true;
            lv.limit = 10000f;
            lv.dampen = 0f;
            lv.drag = c;
            lv.multiplyDragByParticleSize = false;
            lv.multiplyDragByParticleVelocity = true;
        }

        static Gradient AlphaGradient(params (float t, float a)[] keys)
        {
            var g = new Gradient();
            var ak = new GradientAlphaKey[keys.Length];
            for (int i = 0; i < keys.Length; i++) ak[i] = new GradientAlphaKey(keys[i].a, keys[i].t);
            g.SetKeys(new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(Color.white, 1f) }, ak);
            return g;
        }

        static void FadeOut(ParticleSystem ps, float fadeStart)
        {
            var col = ps.colorOverLifetime;
            col.enabled = true;
            col.color = new ParticleSystem.MinMaxGradient(AlphaGradient((0f, 1f), (fadeStart, 1f), (1f, 0f)));
        }

        static void ColorShift(ParticleSystem ps, float fadeStart)
        {
            // Colour keys are replaced at runtime by the palette; only the alpha shape matters here.
            FadeOut(ps, fadeStart);
        }

        static void Shrink(ParticleSystem ps, float endSize)
        {
            var sol = ps.sizeOverLifetime;
            sol.enabled = true;
            var c = new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(0.6f, 1f), new Keyframe(1f, endSize));
            sol.size = new MinMax(1f, c);
        }

        static void Trails(ParticleSystem ps, float lifetimeRatio, float width, bool golden = false)
        {
            var tr = ps.trails;
            tr.enabled = true;
            tr.mode = ParticleSystemTrailMode.PerParticle;
            tr.ratio = 1f;
            tr.lifetime = lifetimeRatio;
            tr.minVertexDistance = 0.5f;
            tr.textureMode = ParticleSystemTrailTextureMode.Stretch;
            tr.dieWithParticles = true;
            tr.sizeAffectsWidth = true;
            tr.sizeAffectsLifetime = false;
            tr.inheritParticleColor = true;
            tr.widthOverTrail = new MinMax(width, new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(1f, 0.05f)));
            var g = new Gradient();
            Color tail = golden ? new Color(1f, 0.55f, 0.18f) : Color.white;
            g.SetKeys(
                new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(tail, 1f) },
                new[] { new GradientAlphaKey(golden ? 0.9f : 0.8f, 0f), new GradientAlphaKey(golden ? 0.45f : 0.25f, 0.5f), new GradientAlphaKey(0f, 1f) });
            tr.colorOverTrail = new ParticleSystem.MinMaxGradient(g);
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.trailMaterial = FxAssets.Trail;
        }

        static void Blink(ParticleSystem ps, float fps)
        {
            var tsa = ps.textureSheetAnimation;
            tsa.enabled = true;
            tsa.mode = ParticleSystemAnimationMode.Grid;
            tsa.numTilesX = 2;
            tsa.numTilesY = 1;
            tsa.animation = ParticleSystemAnimationType.WholeSheet;
            tsa.timeMode = ParticleSystemAnimationTimeMode.FPS;
            tsa.fps = fps;
            tsa.startFrame = new MinMax(0f, 1.99f);
        }

        static ParticleSystem Ring(string name, Transform parent, int count, float speed, float lifeMin, float lifeMax)
        {
            var ps = StarBurst(name, parent, FxAssets.Star, count, speed, speed * 1.02f, lifeMin, lifeMax, 1.0f, 1.2f, 0.045f, 0.12f);
            var sh = ps.shape;
            sh.shapeType = ParticleSystemShapeType.Circle;
            sh.radius = 0.4f;
            sh.radiusThickness = 0f;
            sh.arc = 360f;
            sh.arcMode = ParticleSystemShapeMultiModeValue.BurstSpread;
            return ps;
        }

        // Death sub-emitter: each crossette star breaks into a 4-way cross.
        static ParticleSystem Split(ParticleSystem parent)
        {
            var ps = Make("Split", parent.transform, FxAssets.Star, 200);
            var main = ps.main;
            main.startSpeed = new MinMax(22f, 24f);
            main.startLifetime = new MinMax(0.9f, 1.1f);
            main.startSize = new MinMax(0.9f, 1.1f);
            main.gravityModifier = 0.25f;
            var em = ps.emission;
            em.SetBursts(new[] { new ParticleSystem.Burst(0f, new MinMax(4)) });
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Circle;
            sh.radius = 0.05f;
            sh.radiusThickness = 0f;
            sh.arc = 360f;
            sh.arcMode = ParticleSystemShapeMultiModeValue.BurstSpread;
            Drag(ps, 0.05f);
            FadeOut(ps, 0.6f);
            Trails(ps, 0.45f, 0.8f);
            var inh = ps.inheritVelocity;
            inh.enabled = false;

            var subs = parent.subEmitters;
            subs.enabled = true;
            subs.AddSubEmitter(ps, ParticleSystemSubEmitterType.Death, ParticleSystemSubEmitterProperties.InheritNothing);
            return ps;
        }

        // Second Death sub-emitter: a short white pop where each crossette star breaks.
        static ParticleSystem SplitFlash(ParticleSystem parent)
        {
            var ps = Make("SplitPop", parent.transform, FxAssets.StarHot, 64);
            var main = ps.main;
            main.startLifetime = 0.12f;
            main.startSize = new MinMax(2.6f, 3.2f);
            var em = ps.emission;
            em.SetBursts(new[] { new ParticleSystem.Burst(0f, new MinMax(1)) });
            FadeOut(ps, 0f);
            var subs = parent.subEmitters;
            subs.enabled = true;
            subs.AddSubEmitter(ps, ParticleSystemSubEmitterType.Death, ParticleSystemSubEmitterProperties.InheritNothing);
            return ps;
        }

        // Birth sub-emitter: twinkling sparks shed along each parent star.
        static ParticleSystem Glitter(ParticleSystem parent, float ratePerStar, Color color)
        {
            var ps = Make("Glitter", parent.transform, FxAssets.Glitter, 6000, loop: true);
            var main = ps.main;
            main.duration = 8f;
            main.startSpeed = new MinMax(0.2f, 1.2f);
            main.startLifetime = new MinMax(1.0f, 1.8f);
            main.startSize = new MinMax(0.25f, 0.42f);
            main.startColor = color;
            main.gravityModifier = 0.3f;
            var em = ps.emission;
            em.rateOverTime = ratePerStar;
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Sphere;
            sh.radius = 0.2f;
            Drag(ps, 0.3f);
            FadeOut(ps, 0.4f);
            Blink(ps, 10f);

            var subs = parent.subEmitters;
            subs.enabled = true;
            subs.AddSubEmitter(ps, ParticleSystemSubEmitterType.Birth, ParticleSystemSubEmitterProperties.InheritNothing);
            return ps;
        }

        static ParticleSystem HeadSparks(Transform parent, float rate, bool heavy)
        {
            var ps = Make("Sparks", parent, FxAssets.Spark, heavy ? 700 : 160, loop: true);
            var main = ps.main;
            main.startSpeed = new MinMax(0.4f, heavy ? 3f : 2f);
            main.startLifetime = heavy ? new MinMax(1.2f, 2.4f) : new MinMax(0.3f, 0.75f);
            main.startSize = heavy ? new MinMax(0.35f, 0.65f) : new MinMax(0.25f, 0.45f);
            main.startColor = new Color(1f, 0.68f, 0.32f);
            main.gravityModifier = 0.4f;
            var em = ps.emission;
            em.rateOverTime = rate;
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Sphere;
            sh.radius = 0.15f;
            Drag(ps, 0.4f);
            FadeOut(ps, 0.2f);
            return ps;
        }

        static ParticleSystem HeadCore(Transform parent, float size)
        {
            var ps = Make("Core", parent, FxAssets.StarHot, 1, loop: true, space: ParticleSystemSimulationSpace.Local);
            var main = ps.main;
            main.startLifetime = 1000f;
            main.startSize = size;
            main.startColor = new Color(1f, 0.85f, 0.65f);
            var em = ps.emission;
            em.rateOverTime = 20f;
            return ps;
        }

        // Comet tail: a single ribbon joining recently emitted points.
        static ParticleSystem HeadRibbon(Transform parent)
        {
            var ps = Make("Ribbon", parent, FxAssets.Star, 120, loop: true);
            var main = ps.main;
            main.startLifetime = 0.8f;
            main.startSize = 1.2f;
            var em = ps.emission;
            em.rateOverTime = 45f;
            FadeOut(ps, 0f);
            var sol = ps.sizeOverLifetime;
            sol.enabled = true;
            sol.size = new MinMax(1f, new AnimationCurve(new Keyframe(0f, 1f), new Keyframe(1f, 0.2f)));
            var tr = ps.trails;
            tr.enabled = true;
            tr.mode = ParticleSystemTrailMode.Ribbon;
            tr.ribbonCount = 1;
            tr.textureMode = ParticleSystemTrailTextureMode.Stretch;
            tr.sizeAffectsWidth = true;
            tr.inheritParticleColor = true;
            tr.widthOverTrail = new MinMax(1f);
            tr.colorOverTrail = new ParticleSystem.MinMaxGradient(Color.white);
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.renderMode = ParticleSystemRenderMode.None;
            r.trailMaterial = FxAssets.Trail;
            return ps;
        }

        static ParticleSystem Glow(Transform parent, float size)
        {
            var ps = Make("Glow", parent, FxAssets.Glow, 2);
            var main = ps.main;
            main.startLifetime = 0.9f;
            main.startSize = size;
            main.startColor = new Color(1f, 1f, 1f, 0.38f);
            var em = ps.emission;
            em.SetBursts(new[] { new ParticleSystem.Burst(0f, new MinMax(1)) });
            var col = ps.colorOverLifetime;
            col.enabled = true;
            col.color = new ParticleSystem.MinMaxGradient(AlphaGradient((0f, 1f), (0.15f, 0.7f), (0.5f, 0.2f), (1f, 0f)));
            var sol = ps.sizeOverLifetime;
            sol.enabled = true;
            sol.size = new MinMax(1f, new AnimationCurve(new Keyframe(0f, 0.45f), new Keyframe(0.3f, 1f), new Keyframe(1f, 1.15f)));
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.maxParticleSize = 2f;
            r.sortingFudge = 20f;
            return ps;
        }

        static ParticleSystem Flash(Transform parent)
        {
            var ps = Make("Flash", parent, FxAssets.Star, 2);
            var main = ps.main;
            main.startLifetime = 0.09f;
            main.startSize = 6f;
            main.startColor = new Color(1f, 0.95f, 0.85f);
            var em = ps.emission;
            em.SetBursts(new[] { new ParticleSystem.Burst(0f, new MinMax(1)) });
            var col = ps.colorOverLifetime;
            col.enabled = true;
            col.color = new ParticleSystem.MinMaxGradient(AlphaGradient((0f, 1f), (1f, 0f)));
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.maxParticleSize = 2f;
            return ps;
        }

        static ParticleSystem Smoke(Transform parent)
        {
            var ps = Make("Smoke", parent, FxAssets.Smoke, 16);
            var main = ps.main;
            main.startLifetime = new MinMax(7f, 9f);
            main.startSpeed = new MinMax(2f, 5f);
            main.startSize = new MinMax(16f, 26f);
            main.startRotation = new MinMax(0f, Mathf.PI * 2f);
            main.startColor = new Color(0.20f, 0.20f, 0.23f, 1f);
            main.gravityModifier = -0.01f;
            var em = ps.emission;
            em.SetBursts(new[] { new ParticleSystem.Burst(0.05f, new MinMax(7)) });
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Sphere;
            sh.radius = 7f;
            sh.radiusThickness = 1f;
            Drag(ps, 0.15f);
            var vol = ps.velocityOverLifetime;
            vol.enabled = true;
            vol.space = ParticleSystemSimulationSpace.World;
            vol.x = new MinMax(0.8f, 1.6f);
            vol.y = new MinMax(0.1f, 0.4f);
            vol.z = new MinMax(0f, 0f);
            var col = ps.colorOverLifetime;
            col.enabled = true;
            col.color = new ParticleSystem.MinMaxGradient(AlphaGradient((0f, 0f), (0.12f, 0.16f), (0.5f, 0.11f), (1f, 0f)));
            var sol = ps.sizeOverLifetime;
            sol.enabled = true;
            sol.size = new MinMax(1f, new AnimationCurve(new Keyframe(0f, 0.6f), new Keyframe(1f, 1.6f)));
            var rot = ps.rotationOverLifetime;
            rot.enabled = true;
            rot.z = new MinMax(-0.15f, 0.15f);
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.maxParticleSize = 2f;
            r.sortingFudge = 80f; // draw before the additive sparks so it never dims them
            return ps;
        }

        static ParticleSystem FountainSpray(Transform parent)
        {
            var ps = Make("Spray", parent, FxAssets.Spark, 1400, loop: true);
            var main = ps.main;
            main.startSpeed = new MinMax(14f, 19f);
            main.startLifetime = new MinMax(1.4f, 1.9f);
            main.startSize = new MinMax(0.18f, 0.3f);
            main.gravityModifier = 1f;
            var em = ps.emission;
            em.rateOverTime = 330f;
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Cone;
            sh.angle = 11f;
            sh.radius = 0.35f;
            sh.rotation = new Vector3(-90f, 0f, 0f);
            Drag(ps, 0.006f);
            FadeOut(ps, 0.55f);
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.renderMode = ParticleSystemRenderMode.Stretch;
            r.velocityScale = 0.07f;
            r.lengthScale = 1.0f;
            return ps;
        }

        static ParticleSystem FountainGlow(Transform parent)
        {
            var ps = Make("BaseGlow", parent, FxAssets.Glow, 12, loop: true);
            var main = ps.main;
            main.startLifetime = 0.5f;
            main.startSize = new MinMax(7f, 9f);
            main.startColor = new Color(1f, 1f, 1f, 0.5f);
            var em = ps.emission;
            em.rateOverTime = 10f;
            var col = ps.colorOverLifetime;
            col.enabled = true;
            col.color = new ParticleSystem.MinMaxGradient(AlphaGradient((0f, 0f), (0.3f, 1f), (1f, 0f)));
            ps.transform.localPosition = new Vector3(0f, 1.5f, 0f);
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.maxParticleSize = 2f;
            return ps;
        }

        static ParticleSystem FountainSmoke(Transform parent)
        {
            var ps = Make("Smoke", parent, FxAssets.Smoke, 40, loop: true);
            var main = ps.main;
            main.startLifetime = new MinMax(5f, 7f);
            main.startSpeed = new MinMax(1.5f, 3f);
            main.startSize = new MinMax(6f, 9f);
            main.startRotation = new MinMax(0f, Mathf.PI * 2f);
            main.startColor = new Color(0.22f, 0.21f, 0.22f, 1f);
            var em = ps.emission;
            em.rateOverTime = 2.5f;
            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Cone;
            sh.angle = 20f;
            sh.radius = 1f;
            sh.rotation = new Vector3(-90f, 0f, 0f);
            var vol = ps.velocityOverLifetime;
            vol.enabled = true;
            vol.space = ParticleSystemSimulationSpace.World;
            vol.x = new MinMax(0.6f, 1.2f);
            vol.y = new MinMax(0f, 0f);
            vol.z = new MinMax(0f, 0f);
            var col = ps.colorOverLifetime;
            col.enabled = true;
            col.color = new ParticleSystem.MinMaxGradient(AlphaGradient((0f, 0f), (0.15f, 0.2f), (1f, 0f)));
            var sol = ps.sizeOverLifetime;
            sol.enabled = true;
            sol.size = new MinMax(1f, new AnimationCurve(new Keyframe(0f, 0.5f), new Keyframe(1f, 2.2f)));
            var r = ps.GetComponent<ParticleSystemRenderer>();
            r.maxParticleSize = 2f;
            r.sortingFudge = 80f;
            return ps;
        }
    }
}
