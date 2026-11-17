using UnityEngine;
using UnityEngine.Rendering;

namespace Fireworks
{
    /// <summary>
    /// Applies the night lighting at load (ambient, sky reflection, skybox) without needing baked lighting data,
    /// so a fresh clone looks the same before anyone presses Generate Lighting.
    /// </summary>
    [ExecuteAlways]
    public class NightEnvironment : MonoBehaviour
    {
        public Material skybox;
        [Tooltip("Reflection cubemap used by the water")]
        public Cubemap reflection;
        [ColorUsage(false, true)] public Color ambientSky = new Color(0.020f, 0.026f, 0.050f);
        [ColorUsage(false, true)] public Color ambientHorizon = new Color(0.030f, 0.026f, 0.040f);
        [ColorUsage(false, true)] public Color ambientGround = new Color(0.006f, 0.006f, 0.010f);
        [Range(0f, 2f)] public float reflectionIntensity = 1f;

        void OnEnable() => Apply();

        public void Apply()
        {
            if (skybox != null) RenderSettings.skybox = skybox;
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = ambientSky;
            RenderSettings.ambientEquatorColor = ambientHorizon;
            RenderSettings.ambientGroundColor = ambientGround;
            var sh = new SphericalHarmonicsL2();
            sh.AddAmbientLight(ambientHorizon);
            sh.AddDirectionalLight(Vector3.up, ambientSky - ambientHorizon, 0.5f);
            RenderSettings.ambientProbe = sh;
            RenderSettings.fog = false;
            if (reflection != null)
            {
                RenderSettings.defaultReflectionMode = DefaultReflectionMode.Custom;
                RenderSettings.customReflectionTexture = reflection;
            }
            RenderSettings.reflectionIntensity = reflectionIntensity;
        }
    }
}
