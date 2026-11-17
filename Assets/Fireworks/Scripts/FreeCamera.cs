using UnityEngine;
using UnityEngine.InputSystem;

namespace Fireworks
{
    /// <summary>Optional fly camera: hold right mouse, WASD/QE to move, Shift for speed. Takes over from the director until R restarts.</summary>
    public class FreeCamera : MonoBehaviour
    {
        public ShowCamera director;
        public float moveSpeed = 25f;
        public float fastMultiplier = 4f;
        public float lookSpeed = 0.12f;

        float _yaw, _pitch;
        bool _active;

        void Update()
        {
            var mouse = Mouse.current;
            var kb = Keyboard.current;
            if (mouse == null || kb == null) return;
            if (!mouse.rightButton.isPressed)
            {
                _active = false;
                return;
            }

            if (!_active)
            {
                _active = true;
                var e = transform.eulerAngles;
                _yaw = e.y;
                _pitch = e.x > 180f ? e.x - 360f : e.x;
                if (director != null) director.userControl = true;
            }

            Vector2 d = mouse.delta.ReadValue();
            _yaw += d.x * lookSpeed;
            _pitch = Mathf.Clamp(_pitch - d.y * lookSpeed, -89f, 89f);
            transform.rotation = Quaternion.Euler(_pitch, _yaw, 0f);

            Vector3 move = Vector3.zero;
            if (kb.wKey.isPressed) move += transform.forward;
            if (kb.sKey.isPressed) move -= transform.forward;
            if (kb.dKey.isPressed) move += transform.right;
            if (kb.aKey.isPressed) move -= transform.right;
            if (kb.eKey.isPressed) move += Vector3.up;
            if (kb.qKey.isPressed) move -= Vector3.up;
            float speed = moveSpeed * (kb.leftShiftKey.isPressed ? fastMultiplier : 1f);
            if (move.sqrMagnitude > 0f) transform.position += move.normalized * (speed * Time.unscaledDeltaTime);
        }
    }
}
