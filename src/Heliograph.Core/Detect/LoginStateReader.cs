using System.Text.Json;

namespace Heliograph.Core.Detect;

/// <summary>Reads the dedicated CDP profile's last observed login from state.json. Null when unknown.</summary>
public static class LoginStateReader
{
    public static bool? Read(string? json)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            return null;
        }
        try
        {
            using var document = JsonDocument.Parse(json);
            if (document.RootElement.TryGetProperty("cdp", out var cdp)
                && cdp.TryGetProperty("default", out var profile)
                && profile.TryGetProperty("logged_in", out var value)
                && (value.ValueKind == JsonValueKind.True || value.ValueKind == JsonValueKind.False))
            {
                return value.GetBoolean();
            }
            return null;
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
