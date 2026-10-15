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
            if (!TryObject(document.RootElement, "cdp", out var cdp)
                || !TryObject(cdp, "default", out var profile)
                || !profile.TryGetProperty("logged_in", out var value))
            {
                return null;
            }
            return value.ValueKind is JsonValueKind.True or JsonValueKind.False ? value.GetBoolean() : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    private static bool TryObject(JsonElement parent, string name, out JsonElement child)
    {
        child = default;
        return parent.ValueKind == JsonValueKind.Object
            && parent.TryGetProperty(name, out child)
            && child.ValueKind == JsonValueKind.Object;
    }
}
