using System.Diagnostics;
using Heliograph.Core.Detect;

namespace Heliograph.Core.Detect.Production;

/// <summary>Opens a target with the OS shell. Only the Instagram Store product page is allowed.</summary>
public sealed class ShellLauncher : IShellLauncher
{
    public static bool IsAllowed(string target) => string.Equals(target, InstagramAppDetector.StoreUri, StringComparison.Ordinal);

    public void Open(string target)
    {
        if (!IsAllowed(target))
        {
            throw new ArgumentException("only the Instagram Store product page may be opened", nameof(target));
        }
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true })?.Dispose();
    }
}
