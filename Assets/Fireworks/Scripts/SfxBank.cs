using UnityEngine;

namespace Fireworks
{
    /// <summary>
    /// Sound clip references by contract name (Docs/설계.md, "효과음 파일 이름").
    /// Filled by Fireworks/Build All (or Fireworks/Relink Audio and Cues) from Assets/Fireworks/Audio/SFX.
    /// </summary>
    [CreateAssetMenu(menuName = "Fireworks/Sfx Bank", fileName = "SfxBank")]
    public class SfxBank : ScriptableObject
    {
        [Tooltip("boom_1..4.wav — large bursts (scale ≥ 1)")] public AudioClip[] boom = new AudioClip[0];
        [Tooltip("pop_1..3.wav — small bursts, ring, crossette splits")] public AudioClip[] pop = new AudioClip[0];
        [Tooltip("crackle_1..2.wav — strobe, willow tail")] public AudioClip[] crackle = new AudioClip[0];
        [Tooltip("launch_1..3.wav — mortar launch")] public AudioClip[] launch = new AudioClip[0];
        [Tooltip("whistle_1.wav — comet and some launches")] public AudioClip[] whistle = new AudioClip[0];
        [Tooltip("fountain_loop.wav — seamless fountain loop")] public AudioClip fountainLoop;

        public static readonly string[] BoomNames = { "boom_1", "boom_2", "boom_3", "boom_4" };
        public static readonly string[] PopNames = { "pop_1", "pop_2", "pop_3" };
        public static readonly string[] CrackleNames = { "crackle_1", "crackle_2" };
        public static readonly string[] LaunchNames = { "launch_1", "launch_2", "launch_3" };
        public static readonly string[] WhistleNames = { "whistle_1" };
        public const string FountainLoopName = "fountain_loop";

        public int ClipCount()
        {
            int n = fountainLoop != null ? 1 : 0;
            foreach (var arr in new[] { boom, pop, crackle, launch, whistle })
                if (arr != null)
                    foreach (var c in arr) if (c != null) n++;
            return n;
        }
    }
}
