using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Fireworks.EditorTools
{
    /// <summary>
    /// When the project is opened in the editor with an empty "Untitled" scene
    /// (a fresh clone has no Library, so Unity starts untitled), open the show scene
    /// so the viewer only has to press Play. Runs once per editor session.
    /// </summary>
    [InitializeOnLoad]
    static class OpenShowSceneOnLoad
    {
        const string ScenePath = "Assets/Fireworks/Scenes/Fireworks.unity";
        const string SessionKey = "Fireworks.OpenShowSceneOnLoad.Done";

        static OpenShowSceneOnLoad()
        {
            if (Application.isBatchMode) return;
            if (SessionState.GetBool(SessionKey, false)) return;
            SessionState.SetBool(SessionKey, true);
            EditorApplication.delayCall += OpenIfUntitled;
        }

        static void OpenIfUntitled()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) return;
            Scene active = SceneManager.GetActiveScene();
            if (!string.IsNullOrEmpty(active.path) || active.isDirty) return;
            if (AssetDatabase.LoadAssetAtPath<SceneAsset>(ScenePath) == null) return;
            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        }
    }
}
