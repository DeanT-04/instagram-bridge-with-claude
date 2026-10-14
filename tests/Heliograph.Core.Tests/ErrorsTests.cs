namespace Heliograph.Core.Tests;

public class ErrorsTests
{
    private const string LoginHint =
        "Run `uv run heliograph login` and sign in to Instagram in the window that opens.";

    [Fact]
    public void Each_type_keeps_its_python_name_and_default_hint()
    {
        (HeliographException Error, string ErrorType, string? DefaultHint)[] table =
        [
            (new HeliographException(), "HeliographError", null),
            (new NotLoggedInException(), "NotLoggedInError", LoginHint),
            (new AppNotInstalledException(), "AppNotInstalledError",
                "Run `uv run heliograph setup` to open the Store page, or use the browser (cdp) driver."),
            (new BrowserNotFoundException(), "BrowserNotFoundError",
                "Install Microsoft Edge or Google Chrome, or set HELIOGRAPH_BROWSER_CHANNEL."),
            (new DriverUnavailableException(), "DriverUnavailableError", null),
            (new RateLimitedException(), "RateLimitedError", null),
            (new UnsafeActionException(), "UnsafeActionError", "Write actions require confirm=True."),
            (new DownloadBlockedException(), "DownloadBlockedError", null),
            (new ElementNotFoundException(), "ElementNotFoundError", null),
            (new InstagramApiException("bad status"), "InstagramApiError", null),
        ];

        Assert.All(table, row =>
        {
            Assert.Equal(row.ErrorType, row.Error.ErrorType);
            Assert.Equal(row.DefaultHint, row.Error.Hint);
            Assert.Null(row.Error.TraceId);
        });
    }

    [Fact]
    public void Leaf_types_are_sealed_heliograph_exceptions()
    {
        var leaves = typeof(HeliographException).Assembly.GetTypes()
            .Where(t => t.IsSubclassOf(typeof(HeliographException)))
            .ToList();

        Assert.Equal(9, leaves.Count);
        Assert.All(leaves, t => Assert.True(t.IsSealed, $"{t.Name} is not sealed"));
    }

    [Fact]
    public void Empty_message_falls_back_to_the_summary()
    {
        var error = new DriverUnavailableException();

        Assert.Equal("", error.Detail);
        Assert.Equal("The requested driver cannot run in this environment.", error.Message);
    }

    [Fact]
    public void Message_appends_the_hint()
    {
        var error = new NotLoggedInException("session expired");

        Assert.Equal("session expired", error.Detail);
        Assert.Equal($"session expired (hint: {LoginHint})", error.Message);
    }

    [Fact]
    public void Explicit_hint_overrides_the_default()
    {
        var error = new NotLoggedInException("x", hint: "log in again");

        Assert.Equal("log in again", error.Hint);
        Assert.Equal("x (hint: log in again)", error.Message);
    }

    [Fact]
    public void Empty_hint_suppresses_the_default()
    {
        var error = new UnsafeActionException("x", hint: "");

        Assert.Equal("", error.Hint);
        Assert.Equal("x", error.Message);
    }

    [Fact]
    public void Base_type_can_be_thrown_directly()
    {
        var error = new HeliographException("User 'nobody' not found", hint: "check the spelling");

        Assert.Equal("HeliographError", error.ErrorType);
        Assert.Equal("User 'nobody' not found (hint: check the spelling)", error.Message);
    }

    [Fact]
    public void Rate_limited_carries_retry_after()
    {
        Assert.Null(new RateLimitedException("slow down").RetryAfter);
        Assert.Equal(TimeSpan.FromSeconds(25), new RateLimitedException("x", TimeSpan.FromSeconds(25)).RetryAfter);
    }

    [Fact]
    public void Instagram_api_error_carries_status_and_path()
    {
        var error = new InstagramApiException("x", status: 404, path: "/api/v1/x/");
        Assert.Equal((404, "/api/v1/x/"), (error.Status, error.Path));

        var bare = new InstagramApiException("x");
        Assert.Equal((0, ""), (bare.Status, bare.Path));
    }

    [Fact]
    public void Trace_id_is_attached_after_the_throw()
    {
        var error = new ElementNotFoundException("no like button") { TraceId = "abc123" };

        Assert.Equal("abc123", error.TraceId);
    }

    [Fact]
    public void Inner_exception_is_kept()
    {
        var cause = new TimeoutException("lock busy");
        var error = new RateLimitedException("busy", TimeSpan.FromSeconds(1), hint: cause.Message, inner: cause);

        Assert.Same(cause, error.InnerException);
    }
}
