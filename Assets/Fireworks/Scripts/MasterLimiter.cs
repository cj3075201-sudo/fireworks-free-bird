using UnityEngine;

namespace Fireworks
{
    /// <summary>
    /// Safety peak limiter on the final mix (sits next to the AudioListener).
    /// Dense bursts stacked on loud music can sum past full scale; this pulls those
    /// moments down to the ceiling instantly and lets the gain recover over the release time.
    /// Below the ceiling it does nothing. ShowAudio adds it at runtime when it is missing.
    /// </summary>
    [RequireComponent(typeof(AudioListener))]
    public class MasterLimiter : MonoBehaviour
    {
        [Tooltip("Highest output peak, dBFS")]
        [Range(-6f, 0f)] public float ceilingDb = -1f;
        [Tooltip("Seconds for the gain to recover after a peak")]
        [Range(0.02f, 1f)] public float releaseSeconds = 0.15f;

        float _ceiling = 1f;
        float _releaseCoef = 0.999f;
        float _gain = 1f;
        float _blockMinGain = 1f; // written by the audio thread, read by MixPanel

        /// <summary>Deepest gain reduction in the last audio block, dB (0 = not limiting).</summary>
        public float ReductionDb => -20f * Mathf.Log10(Mathf.Max(1e-4f, _blockMinGain));

        void OnEnable() => Recalc();
        void OnValidate() => Recalc();

        void Recalc()
        {
            _ceiling = Mathf.Pow(10f, ceilingDb / 20f);
            int sr = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;
            _releaseCoef = Mathf.Exp(-1f / (releaseSeconds * sr));
        }

        // Audio thread: no allocations, no Unity API calls.
        void OnAudioFilterRead(float[] data, int channels)
        {
            float ceiling = _ceiling, coef = _releaseCoef, gain = _gain, minGain = 1f;
            for (int i = 0; i < data.Length; i += channels)
            {
                float peak = 0f;
                for (int c = 0; c < channels; c++)
                {
                    float a = data[i + c] < 0f ? -data[i + c] : data[i + c];
                    if (a > peak) peak = a;
                }
                float want = peak > ceiling ? ceiling / peak : 1f;
                gain = want < gain ? want : want + (gain - want) * coef;
                if (gain < minGain) minGain = gain;
                for (int c = 0; c < channels; c++) data[i + c] *= gain;
            }
            _gain = gain;
            _blockMinGain = minGain;
        }
    }
}
