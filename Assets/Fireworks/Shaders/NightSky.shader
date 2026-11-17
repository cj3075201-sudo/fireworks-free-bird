// Procedural night skybox: vertical gradient, warm city glow on the horizon, hashed stars with gentle twinkle.
// No texture fetches; one hash per pixel. Mobile-safe (ALU only), works on any URP target.
Shader "Fireworks/NightSky"
{
    Properties
    {
        // Colours are [HDR] so they stay linear (no sRGB conversion); the factory writes linear values.
        [HDR] _ZenithColor ("Zenith", Color) = (0.0035, 0.0045, 0.013, 1)
        [HDR] _HorizonColor ("Horizon", Color) = (0.030, 0.027, 0.050, 1)
        // City glow: light pollution above the skyline, strongest toward +Z.
        [HDR] _GlowColor ("City Glow", Color) = (0.085, 0.050, 0.050, 1)
        _GlowHeight ("City Glow Falloff", Range(1, 40)) = 11
        [HDR] _GroundColor ("Below Horizon", Color) = (0.002, 0.0025, 0.004, 1)
        // Star grid: cells per unit of view direction (higher = more, denser stars).
        _StarGrid ("Star Grid", Range(40, 600)) = 95
        // Star chance: fraction of cells that hold a star.
        _StarChance ("Star Chance", Range(0, 1)) = 0.11
        _StarBrightness ("Star Brightness", Range(0, 2)) = 0.5
        // Star size: radius in screen pixels, resolution independent.
        _StarPixels ("Star Size (px)", Range(0.3, 3)) = 0.9
        _Twinkle ("Twinkle", Range(0, 1)) = 0.35
    }

    SubShader
    {
        Tags { "Queue" = "Background" "RenderType" = "Background" "PreviewType" = "Skybox" "RenderPipeline" = "UniversalPipeline" }
        Cull Off
        ZWrite Off

        Pass
        {
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            CBUFFER_START(UnityPerMaterial)
                half4 _ZenithColor;
                half4 _HorizonColor;
                half4 _GlowColor;
                half _GlowHeight;
                half4 _GroundColor;
                float _StarGrid;
                half _StarChance;
                half _StarBrightness;
                half _StarPixels;
                half _Twinkle;
            CBUFFER_END

            struct Attributes
            {
                float4 positionOS : POSITION;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float3 dir : TEXCOORD0;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            Varyings vert(Attributes v)
            {
                Varyings o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.positionCS = TransformObjectToHClip(v.positionOS.xyz);
                o.dir = v.positionOS.xyz;
                return o;
            }

            float Hash13(float3 p)
            {
                p = frac(p * 0.1031);
                p += dot(p, p.zyx + 31.32);
                return frac((p.x + p.y) * p.z);
            }

            float3 Hash33(float3 p)
            {
                p = frac(p * float3(0.1031, 0.1030, 0.0973));
                p += dot(p, p.yxz + 33.33);
                return frac((p.xxy + p.yxx) * p.zyx);
            }

            half3 SkyGradient(float3 d)
            {
                float y = d.y;
                half3 col;
                if (y >= 0.0)
                {
                    col = lerp(_HorizonColor.rgb, _ZenithColor.rgb, pow(saturate(y), 0.45));
                    float glow = exp(-y * _GlowHeight) * saturate(0.35 + 0.65 * d.z);
                    col += _GlowColor.rgb * glow;
                }
                else
                {
                    col = lerp(_HorizonColor.rgb * 0.7, _GroundColor.rgb, saturate(-y * 8.0));
                }
                return col;
            }

            half4 frag(Varyings i) : SV_Target
            {
                float3 d = normalize(i.dir);
                half3 col = SkyGradient(d);

                // Stars: one jittered point per grid cell, projected onto the unit sphere.
                float3 p = d * _StarGrid;
                float3 cell = floor(p);
                float exists = step(1.0 - _StarChance, Hash13(cell + 17.17));
                float3 h = Hash33(cell);
                float3 star = normalize(cell + 0.2 + 0.6 * h) * _StarGrid;
                float dist = length(p - star);
                float pix = max(length(fwidth(p)), 1e-4);
                float core = exp(-pow(dist / (pix * _StarPixels), 2.0));
                float mag = pow(Hash13(cell + 3.7), 5.0) * 0.9 + 0.06;
                float tw = 1.0 - _Twinkle * (0.5 + 0.5 * sin(_Time.y * (1.5 + 3.0 * h.x) + h.y * 40.0));
                float horizonFade = saturate(d.y * 6.0);
                col += exists * core * mag * tw * horizonFade * _StarBrightness;

                return half4(col, 1.0);
            }
            ENDHLSL
        }
    }
    Fallback Off
}
