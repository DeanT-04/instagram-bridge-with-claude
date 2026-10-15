using Heliograph.Core.Detect;
using System.Diagnostics;

namespace Heliograph.Core.Detect.Production;

/// <summary>Opens a target with the OS shell. Only the Instagram Store product page is allowed.</summary>
public sealed class ShellLauncher : IShellLauncher
{
    private const string StorePrefix = "ms-windows-store://pdp/?ProductId=";
    private const int MaxLength = 200;

    public static bool IsAllowed(string target) =>
        target.Length <= MaxLength
        && target.StartsWith(StorePrefix, StringComparison.Ordinal)
        && target.AsSpan(StorePrefix.Length).ToString().All(char.IsAsciiLetterOrDigit)
        && target.Length > StorePrefix.Length;

    public void Open(string target)
    {
        if (!IsAllowed(target))
        {
            throw new ArgumentException("only the Instagram Store product page may be opened", nameof(target));
        }
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true })?.Dispose();
    }
}
