namespace Heliograph.Core;

public class HeliographException : Exception
{
    public HeliographException(string message = "", string? hint = null, Exception? inner = null)
        : this("HeliographError", "Base class for all Heliograph errors.", null, message, hint, inner)
    {
    }

    private protected HeliographException(
        string errorType, string summary, string? defaultHint, string message, string? hint, Exception? inner)
        : base(Describe(message.Length > 0 ? message : summary, hint ?? defaultHint), inner)
    {
        ErrorType = errorType;
        Summary = summary;
        Detail = message;
        Hint = hint ?? defaultHint;
    }

    /// <summary>The Python class name, which error text shown to Claude and the eye trace use.</summary>
    public string ErrorType { get; }

    public string Summary { get; }

    public string Detail { get; }

    public string? Hint { get; }

    public string? TraceId { get; set; }

    private static string Describe(string text, string? hint) =>
        string.IsNullOrEmpty(hint) ? text : $"{text} (hint: {hint})";
}

public sealed class NotLoggedInException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "NotLoggedInError",
        "The Instagram session is not logged in.",
        "Run `uv run heliograph login` and sign in to Instagram in the window that opens.",
        message, hint, inner);

public sealed class AppNotInstalledException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "AppNotInstalledError",
        "The Microsoft Store Instagram app is not installed.",
        "Run `uv run heliograph setup` to open the Store page, or use the browser (cdp) driver.",
        message, hint, inner);

public sealed class BrowserNotFoundException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "BrowserNotFoundError",
        "No supported Chromium browser (Edge/Chrome) was found.",
        "Install Microsoft Edge or Google Chrome, or set HELIOGRAPH_BROWSER_CHANNEL.",
        message, hint, inner);

public sealed class DriverUnavailableException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "DriverUnavailableError",
        "The requested driver cannot run in this environment.",
        null,
        message, hint, inner);

public sealed class RateLimitedException(
    string message = "", TimeSpan? retryAfter = null, string? hint = null, Exception? inner = null)
    : HeliographException(
        "RateLimitedError",
        "An operation was refused by Heliograph's rate limiter or by Instagram.",
        null,
        message, hint, inner)
{
    public TimeSpan? RetryAfter { get; } = retryAfter;
}

public sealed class UnsafeActionException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "UnsafeActionError",
        "A write action was attempted without explicit confirmation or violates a safety rule.",
        "Write actions require confirm=True.",
        message, hint, inner);

public sealed class DownloadBlockedException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "DownloadBlockedError",
        "A download URL was rejected (non-HTTPS or host not in the Instagram CDN allow-list).",
        null,
        message, hint, inner);

public sealed class ElementNotFoundException(string message = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "ElementNotFoundError",
        "A UI element or DOM node could not be located.",
        null,
        message, hint, inner);

public sealed class InstagramApiException(
    string message, int status = 0, string path = "", string? hint = null, Exception? inner = null)
    : HeliographException(
        "InstagramApiError",
        "Instagram answered with an unexpected status or a non-JSON body.",
        null,
        message, hint, inner)
{
    public int Status { get; } = status;

    public string Path { get; } = path;
}
