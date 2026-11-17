using UnityEngine;
using UnityEngine.UI;

namespace Fireworks
{
    /// <summary>Black fade, title, hit flash and end credit. Alphas are driven by ShowController every frame.</summary>
    public class ShowUI : MonoBehaviour
    {
        public Image fader;
        public Image flash;
        public CanvasGroup titleGroup;
        public Text titleText;
        public Text subtitleText;
        public CanvasGroup creditGroup;
        public Text creditText;
        public Text hintText;

        public void SetTitle(string title, string subtitle)
        {
            if (titleText != null) titleText.text = title;
            if (subtitleText != null) subtitleText.text = subtitle;
        }

        public void SetCredit(string credit)
        {
            if (creditText != null) creditText.text = credit;
        }

        public void SetAlphas(float fade, float title, float credit)
        {
            if (fader != null) fader.color = new Color(0f, 0f, 0f, Mathf.Clamp01(fade));
            if (titleGroup != null) titleGroup.alpha = Mathf.Clamp01(title);
            if (creditGroup != null) creditGroup.alpha = Mathf.Clamp01(credit);
        }

        public void SetFlash(Color tint, float amount)
        {
            if (flash == null) return;
            tint.a = Mathf.Clamp01(amount);
            flash.color = tint;
            flash.enabled = amount > 0.002f;
        }
    }
}
