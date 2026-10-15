using Heliograph.Core.Detect;
using System.Runtime.InteropServices;

namespace Heliograph.Core.Detect.Production;

/// <summary>
/// Titles of visible top-level windows whose process runs in the package family named by the AUMID.
/// Matching is by package family, the same identity the AUMID carries, so a browser tab titled "Instagram" never matches.
/// </summary>
public sealed unsafe class AppWindowProbe : IAppWindowProbe
{
    private const uint ProcessQueryLimitedInformation = 0x1000;
    private const int MaxFamilyLength = 256;
    private const int MaxTitleLength = 512;

    [ThreadStatic]
    private static Collector? _collector;

    private sealed class Collector(string family)
    {
        public string Family { get; } = family;

        public List<string> Titles { get; } = [];
    }

    public IReadOnlyList<string> FindWindowTitles(string aumid)
    {
        if (!OperatingSystem.IsWindows())
        {
            return [];
        }
        var bang = aumid.IndexOf('!');
        if (bang <= 0)
        {
            return [];
        }

        var collector = new Collector(aumid[..bang]);
        _collector = collector;
        try
        {
            NativeMethods.EnumWindows(&Visit, 0);
        }
        finally
        {
            _collector = null;
        }
        return collector.Titles;
    }

    [UnmanagedCallersOnly]
    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static int Visit(nint hwnd, nint _)
    {
        var collector = _collector;
        if (collector is null || NativeMethods.IsWindowVisible(hwnd) == 0)
        {
            return 1;
        }
        NativeMethods.GetWindowThreadProcessId(hwnd, out var pid);
        var process = NativeMethods.OpenProcess(ProcessQueryLimitedInformation, false, pid);
        if (process == 0)
        {
            return 1;
        }
        try
        {
            if (FamilyOf(process) != collector.Family)
            {
                return 1;
            }
            var title = TitleOf(hwnd);
            if (title.Length > 0)
            {
                collector.Titles.Add(title);
            }
        }
        finally
        {
            NativeMethods.CloseHandle(process);
        }
        return 1;
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static string? FamilyOf(nint process)
    {
        uint length = MaxFamilyLength;
        var buffer = stackalloc char[MaxFamilyLength];
        var status = NativeMethods.GetPackageFamilyName(process, ref length, buffer);
        return status == 0 && length > 0 ? new string(buffer, 0, (int)length - 1) : null;
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static string TitleOf(nint hwnd)
    {
        var buffer = stackalloc char[MaxTitleLength];
        var copied = NativeMethods.GetWindowTextW(hwnd, buffer, MaxTitleLength);
        return copied > 0 ? new string(buffer, 0, copied) : string.Empty;
    }
}

[System.Runtime.Versioning.SupportedOSPlatform("windows")]
internal static unsafe partial class NativeMethods
{
    [LibraryImport("user32.dll")]
    internal static partial int EnumWindows(delegate* unmanaged<nint, nint, int> callback, nint lParam);

    [LibraryImport("user32.dll")]
    internal static partial int IsWindowVisible(nint hwnd);

    [LibraryImport("user32.dll")]
    internal static partial uint GetWindowThreadProcessId(nint hwnd, out uint processId);

    [LibraryImport("user32.dll", StringMarshalling = StringMarshalling.Utf16)]
    internal static partial int GetWindowTextW(nint hwnd, char* text, int maxCount);

    [LibraryImport("kernel32.dll", SetLastError = true)]
    internal static partial nint OpenProcess(uint access, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint processId);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static partial bool CloseHandle(nint handle);

    [LibraryImport("kernel32.dll", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    internal static partial int GetPackageFamilyName(nint process, ref uint length, char* familyName);
}
