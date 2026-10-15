using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Heliograph.Core.Detect;

namespace Heliograph.Core.Detect.Production;

/// <summary>
/// Titles of visible top-level windows whose AppUserModelID equals the requested AUMID. The Store Instagram app is a
/// hosted Edge PWA, so its windows belong to the shared msedge process; only the per-window AUMID identifies them.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed unsafe class AppWindowProbe : IAppWindowProbe
{
    private const string ChromeWindowClass = "Chrome_WidgetWin_1";
    private const int MaxTitleLength = 512;
    private const int MaxClassLength = 256;

    [ThreadStatic]
    private static Collector? _collector;

    private sealed class Collector(string aumid)
    {
        public string Aumid { get; } = aumid;

        public List<string> Titles { get; } = [];
    }

    public IReadOnlyList<string> FindWindowTitles(string aumid)
    {
        if (!OperatingSystem.IsWindows() || string.IsNullOrEmpty(aumid))
        {
            return [];
        }

        var collector = new Collector(aumid);
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
    private static int Visit(nint hwnd, nint _)
    {
        try
        {
            var collector = _collector;
            if (collector is null || NativeMethods.IsWindowVisible(hwnd) == 0 || ClassOf(hwnd) != ChromeWindowClass)
            {
                return 1;
            }
            if (AumidOf(hwnd) != collector.Aumid)
            {
                return 1;
            }
            var title = TitleOf(hwnd);
            if (title.Length > 0)
            {
                collector.Titles.Add(title);
            }
            return 1;
        }
        catch (Exception)
        {
            return 1;
        }
    }

    /// <summary>Reads PKEY_AppUserModel_ID from the window's property store. Null when the window has none.</summary>
    private static string? AumidOf(nint hwnd)
    {
        var iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
        nint store = 0;
        if (NativeMethods.SHGetPropertyStoreForWindow(hwnd, &iid, &store) < 0 || store == 0)
        {
            return null;
        }

        try
        {
            var vtable = *(nint**)store;
            var getValue = (delegate* unmanaged[Stdcall]<nint, PropertyKey*, PropVariant*, int>)vtable[5];
            PropVariant value = default;
            if (getValue(store, &key, &value) < 0)
            {
                return null;
            }
            try
            {
                return value.Vt == NativeMethods.VtLpwstr && value.Ptr != 0 ? Marshal.PtrToStringUni(value.Ptr) : null;
            }
            finally
            {
                NativeMethods.PropVariantClear(&value);
            }
        }
        finally
        {
            var release = (delegate* unmanaged[Stdcall]<nint, uint>)(*(nint**)store)[2];
            release(store);
        }
    }

    private static string ClassOf(nint hwnd)
    {
        var buffer = stackalloc char[MaxClassLength];
        var copied = NativeMethods.GetClassNameW(hwnd, buffer, MaxClassLength);
        return copied > 0 ? new string(buffer, 0, copied) : string.Empty;
    }

    private static string TitleOf(nint hwnd)
    {
        var buffer = stackalloc char[MaxTitleLength];
        var copied = NativeMethods.GetWindowTextW(hwnd, buffer, MaxTitleLength);
        return copied > 0 ? new string(buffer, 0, copied) : string.Empty;
    }
}

[StructLayout(LayoutKind.Sequential)]
internal readonly struct PropertyKey(Guid format, uint id)
{
    public readonly Guid Format = format;
    public readonly uint Id = id;
}

/// <summary>PROPVARIANT. Only the tag and the pointer payload are read; the tail pads the union for the callee.</summary>
[StructLayout(LayoutKind.Sequential)]
internal struct PropVariant
{
    public ushort Vt;
    public ushort Reserved1;
    public ushort Reserved2;
    public ushort Reserved3;
    public nint Ptr;
    public nint Tail;
}

[SupportedOSPlatform("windows")]
internal static unsafe partial class NativeMethods
{
    internal const ushort VtLpwstr = 31;

    [LibraryImport("user32.dll")]
    internal static partial int EnumWindows(delegate* unmanaged<nint, nint, int> callback, nint lParam);

    [LibraryImport("user32.dll")]
    internal static partial int IsWindowVisible(nint hwnd);

    [LibraryImport("user32.dll", StringMarshalling = StringMarshalling.Utf16)]
    internal static partial int GetClassNameW(nint hwnd, char* className, int maxCount);

    [LibraryImport("user32.dll", StringMarshalling = StringMarshalling.Utf16)]
    internal static partial int GetWindowTextW(nint hwnd, char* text, int maxCount);

    [LibraryImport("shell32.dll")]
    internal static partial int SHGetPropertyStoreForWindow(nint hwnd, Guid* riid, nint* store);

    [LibraryImport("ole32.dll")]
    internal static partial int PropVariantClear(PropVariant* value);
}
