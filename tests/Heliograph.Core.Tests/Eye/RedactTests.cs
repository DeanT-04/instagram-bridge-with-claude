using System.Text.Json;
using System.Text.Json.Nodes;
using Heliograph.Core.Eye;

namespace Heliograph.Core.Tests.Eye;

public sealed class RedactTests
{
    private const string Redacted = Redaction.Redacted;

    [Theory]
    [InlineData("cookie")]
    [InlineData("Cookie")]
    [InlineData("Set-Cookie")]
    [InlineData("sessionid")]
    [InlineData("csrftoken")]
    [InlineData("X-CSRFToken")]
    [InlineData("Authorization")]
    [InlineData("authorization")]
    [InlineData("Proxy-Authorization")]
    [InlineData("password")]
    [InlineData("PASSWORD")]
    [InlineData("user_password")]
    [InlineData("passwd")]
    [InlineData("api_key")]
    [InlineData("apiKey")]
    [InlineData("X-API-Key")]
    [InlineData("access_token")]
    [InlineData("refreshToken")]
    [InlineData("client_secret")]
    [InlineData("secret")]
    [InlineData("ds_user_id")]
    [InlineData("x-ig-www-claim")]
    [InlineData("cookies")]
    [InlineData("private_key")]
    public void Sensitive_keys(string key) => Assert.True(Redaction.IsSensitiveKey(key));

    [Theory]
    [InlineData("username")]
    [InlineData("url")]
    [InlineData("caption")]
    [InlineData("max_tokens")]
    [InlineData("like_count")]
    [InlineData("media_id")]
    [InlineData("")]
    [InlineData("name")]
    public void Non_sensitive_keys(string key) => Assert.False(Redaction.IsSensitiveKey(key));

    [Fact]
    public void Redacts_nested_structures_recursively()
    {
        var first = new Dictionary<string, object?> { ["password"] = "hunter2", ["user"] = "bob" };
        var data = new Dictionary<string, object?>
        {
            ["headers"] = new Dictionary<string, object?>
            {
                ["Cookie"] = "sessionid=abc; csrftoken=def",
                ["Accept"] = "application/json",
            },
            ["items"] = new object?[] { first, ("x", new Dictionary<string, object?> { ["token"] = "t0k" }) },
            ["count"] = 3,
            ["ok"] = true,
            ["none"] = null,
        };

        var output = Redaction.Redact(data)!;

        Assert.Equal(Redacted, (string?)output["headers"]!["Cookie"]);
        Assert.Equal("application/json", (string?)output["headers"]!["Accept"]);
        AssertJson("""{"password":"[REDACTED]","user":"bob"}""", output["items"]![0]);
        Assert.Equal(Redacted, (string?)output["items"]![1]![1]!["token"]);
        Assert.Equal(3, (int)output["count"]!);
        Assert.True((bool)output["ok"]!);
        Assert.True(output.AsObject().ContainsKey("none"));
        Assert.Null(output["none"]);
        Assert.Equal("hunter2", first["password"]);
    }

    [Fact]
    public void Empty_sensitive_values_are_left_alone() =>
        AssertJson(
            """{"password":null,"token":""}""",
            Redaction.Redact(new Dictionary<string, object?> { ["password"] = null, ["token"] = "" }));

    [Theory]
    [InlineData("Authorization: Bearer IGT:2:eyJkc191c2VyX2lkIjoiMTIzIn0=", "IGT:2:eyJ")]
    [InlineData("header bearer abcdef123456 trailing", "abcdef123456")]
    [InlineData("Cookie: sessionid=123%3Aabc%3A12; csrftoken=zzz", "123%3Aabc")]
    [InlineData("url?x=1&sessionid=987654321%3AAbCdEf&y=2", "987654321")]
    [InlineData("csrftoken=Q2xpZW50U2VjcmV0 rest", "Q2xpZW50U2VjcmV0")]
    [InlineData("""{"password": "hunter2", "a": 1}""", "hunter2")]
    [InlineData("password=hunter2&next=/", "hunter2")]
    [InlineData("api_key: sk-verysecretvalue1234567890", "sk-verysecret")]
    [InlineData("access_token=EAAB1234abcd", "EAAB1234abcd")]
    [InlineData("jwt eyJ-fake-header.eyJ-fake-body.fake-signature-value", "eyJ-fake-header")]
    [InlineData("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv", "sk-ant-api03")]
    [InlineData("gh ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "ghp_AbCd")]
    [InlineData("random Xy7Kq9Lm2Np4Rs6Tv8Wx0Za1Bc3De5Fg7Hi9 end", "Xy7Kq9Lm2Np4")]
    public void Redact_text_masks_secrets(string text, string secret) => AssertMasked(text, secret);

    [Theory]
    [InlineData("https://www.instagram.com/reel/C8abcDEF123/")]
    [InlineData("sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08")]
    [InlineData("media 3412345678901234567_123456789")]
    [InlineData(@"C:\Users\alice\.heliograph\eye\artifacts\screenshot.png")]
    [InlineData("a normal sentence about tokens and cookies")]
    public void Redact_text_leaves_benign_text(string text) => Assert.Equal(text, Redaction.RedactText(text));

    [Fact]
    public void Redacts_serialized_models_and_bytes()
    {
        var creds = new Creds("u", "p");
        var data = new Dictionary<string, object?>
        {
            ["creds"] = JsonSerializer.SerializeToNode(creds, Web),
            ["record"] = creds,
            ["blob"] = new byte[10],
        };

        var output = Redaction.Redact(data)!;

        AssertJson("""{"username":"u","password":"[REDACTED]"}""", output["creds"]);
        Assert.Equal("Creds { Username = u, Password = [REDACTED] }", (string?)output["record"]);
        Assert.Equal("<10 bytes>", (string?)output["blob"]);
    }

    [Fact]
    public void Deep_nesting_does_not_explode()
    {
        var deep = new Dictionary<string, object?>();
        var current = deep;
        for (var i = 0; i < 100; i++)
        {
            var next = new Dictionary<string, object?>();
            current["x"] = next;
            current = next;
        }

        var output = Redaction.Redact(deep);

        Assert.NotNull(output);
        Assert.Contains("[MAX_DEPTH]", output.ToJsonString(), StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("https://scontent.cdninstagram.com/v/x.mp4?_nc_ohc=AbCdEf12345&oh=00_AYBcd99&oe=66A1B2C3", "00_AYBcd99")]
    [InlineData("https://scontent.cdninstagram.com/v/x.mp4?_nc_ohc=AbCdEf12345&oe=66A1B2C3", "AbCdEf12345")]
    [InlineData("https://x.fbcdn.net/y.jpg?efg=eyJ2ZW5jb2RlX3RhZyI6&oe=66A1B2C3", "66A1B2C3")]
    [InlineData("{'Cookie': 'abc123def'}", "abc123def")]
    [InlineData("{'authorization': 'abc123def456'}", "abc123def456")]
    [InlineData("""{"ds_user_id": "123456789"}""", "123456789")]
    [InlineData("headers={'X-IG-WWW-Claim': 'hmac.AR3abcdef'}", "hmac.AR3abcdef")]
    [InlineData("Cookie(sessionid='12345%3Aabc')", "12345%3Aabc")]
    [InlineData("sessionid%3D12345%3AabcDEF", "12345%3AabcDEF")]
    [InlineData("ig-set-authorization: IGT:2:eyJxxxxxxxx", "eyJxxxxxxxx")]
    [InlineData("token is IGT:2:eyJkc191c2VyX2lk", "eyJkc191c2VyX2lk")]
    [InlineData("enc_password=#PWD_INSTAGRAM_BROWSER:10:1700000000:AbCd", "#PWD_INSTAGRAM")]
    [InlineData("pwd='placeholder-pw'", "placeholder-pw")]
    public void Redact_text_security_review_gaps(string text, string secret) => AssertMasked(text, secret);

    [Fact]
    public void Signed_url_keeps_path() =>
        Assert.StartsWith(
            "https://scontent.cdninstagram.com/v/t51/clip.mp4?oh=",
            Redaction.RedactText("https://scontent.cdninstagram.com/v/t51/clip.mp4?oh=00_AYB123&oe=66A1"),
            StringComparison.Ordinal);

    [Theory]
    [InlineData("IG-Set-Authorization")]
    [InlineData("IG-U-DS-USER-ID")]
    [InlineData("ig-u-rur")]
    [InlineData("X-MID")]
    [InlineData("enc_password")]
    public void Instagram_header_keys_sensitive(string key) => Assert.True(Redaction.IsSensitiveKey(key));

    [Fact]
    public void Cookie_name_value_records_are_redacted()
    {
        var cookies = new object?[]
        {
            new Dictionary<string, object?> { ["name"] = "sessionid", ["value"] = "123%3Aabc", ["domain"] = ".instagram.com" },
            new Dictionary<string, object?> { ["name"] = "ds_user_id", ["value"] = "42" },
            new Dictionary<string, object?> { ["name"] = "ig_lang", ["value"] = "en" },
        };

        var output = Redaction.Redact(new Dictionary<string, object?> { ["items"] = cookies })!;

        AssertJson("""{"name":"sessionid","value":"[REDACTED]","domain":".instagram.com"}""", output["items"]![0]);
        Assert.Equal(Redacted, (string?)output["items"]![1]!["value"]);
        Assert.Equal("en", (string?)output["items"]![2]!["value"]);
    }

    private static readonly JsonSerializerOptions Web = new(JsonSerializerDefaults.Web);

    private static void AssertMasked(string text, string secret)
    {
        var output = Redaction.RedactText(text);
        Assert.DoesNotContain(secret, output, StringComparison.Ordinal);
        Assert.Contains(Redacted, output, StringComparison.Ordinal);
    }

    internal static void AssertJson(string expected, JsonNode? actual) =>
        Assert.True(
            JsonNode.DeepEquals(JsonNode.Parse(expected), actual),
            $"expected {expected}\nactual   {actual?.ToJsonString()}");

    private sealed record Creds(string Username, string Password);
}
