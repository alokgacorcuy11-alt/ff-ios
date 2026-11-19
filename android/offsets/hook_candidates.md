# Hook candidates, read out of XYZ's own patch

Source: `assets/Assembly-CSharp-patch.bytes` inside `com.xyz.app` 3.0 (50),
`a48d5260...f19ce2`, decrypted with `AssetCipher` (see `android/README.md` §8b).
119,036 bytes of IFix patch for `Assembly-CSharp, Version=0.86.0.518`.

This is a **name list, not an address list**. A patch resolves names against the
target assembly when it loads, so it carries none of the offsets - which is
exactly why the module needs the client binary it is aimed at, not the panel
that drops it. What the patch does give is the complete set of members that are
worth resolving, in the order they matter.

## Player

| member | use |
|---|---|
| `CurrentLocalPlayer` | the local player singleton; the frame entry point |
| `get_RootTransform` | world position |
| `IsLocalPlayer` | filters the local player out of the entity list |
| `IsLocalTeammate` | keeps teammates out of ESP |
| `get_IsDieing` / `get_IsDead` | skip corpses |
| `get_NickName` | ESP name |
| `get_CurHP` / `get_MaxHP` | health bar, and the fill ratio |

## Bones - the skeleton and the aim point

`GetHeadTF`, `get_HeadCollider`, `get_NeckBone`, `get_SpineBoneTransform`,
`GetChestPos`, `GetHipTF`, `get_ArmBoneLeft`, `get_ArmBoneRight`,
`get_LeftForeArmTF`, `get_RightForeArmTF`, `get_HandBoneLeft`,
`get_HandBoneRight`, `get_LeftHandTF`, `get_RightHandTF`,
`get_LegBoneLeft`, `get_LegBoneRight`, `get_FootBoneLeft`, `get_FootBoneRight`,
`GetLeftAnkleTF`, `GetRightAnkleTF`, `GetLeftToeTF`, `GetRightToeTF`,
`get_HipsBoneTransform`

Eighteen points plus the head: enough for a full skeleton and for choosing an
aim bone per mode (head / neck / chest / body).

## Weapon and aim

| member | use |
|---|---|
| `GetActiveWeapon` | current weapon instance |
| `IsFiring` | only correct while actually shooting |
| `IsHoldingFireForSingleShot` | the drag-headshot path |
| `get_InSwapWeaponCD` / `ResetSwapWeaponTime` | fast switch |
| `SetAimRotation` | the aim write - and the single loudest behavioural tell |

`SetAimRotation` is what the server sees. Anything built on it has to move in
steps, not snap; a 180° jump in one frame is the signature of every aimbot ban
that is not a hash match.

## Screen projection

`WorldToScreenPoint`, `ScreenToWorldPoint`, `get_fieldOfView`, `get_aspect`,
`get_orthographic`, `get_orthographicSize`, `Distance`

The patch reads the camera every frame instead of hardcoding a projection, which
is why it survives resolution and FOV changes.

## Rendering, two paths

| path | members | consequence |
|---|---|---|
| world space | `LineRenderer`, `TextMesh`, `Hidden/Internal-Colored` shader, `_ZTest`, `_ZWrite` | drawn by the engine, depth-tested, visible in screenshots |
| screen space | `android.widget.ImageView`, `android.graphics.Canvas`, `Paint`, `Bitmap.createBitmap`, `addContentView`, `setImageBitmap` | **an Android view added by the game's own uid** |

The second path is a hard tell: the game's process owning a view it never
created is visible to the game and, in principle, to anything enumerating views
for that uid.

## Its own control surface

The patch starts an HTTP server in-process:

```
HTTP/1.1 200 OK
Content-Type: text/plain; charset=utf-8
Access-Control-Allow-Origin: *
/item1  /item1/desativar   ...   /item5  /item5/desativar
```

So the panel is a WebView pointed at `127.0.0.1`, and `/desativar` ("disable")
is a toggle endpoint. The APK in the archive is therefore: a dropper, an ADB
client, and a browser - no cheat logic at all.

It also carries a licence gate it never uses (`AUTH_KEY_INVALID`,
`AUTH_FILE_INVALID_OR_EXPIRED`, `/auth.txt`, `KEY2P{"valid":true}`), disabled by
`SealGuard` returning `1`.

## What this means for the build

Two consequences, and both change what to ask for next:

1. **The target is a Mono assembly, not IL2CPP.** `Assembly-CSharp` and IFix
   only exist in a Unity build with the Mono scripting backend. A store Free
   Fire is IL2CPP, so the client this patch runs in is not the store client.
   The dump needed is therefore `assets/bin/Data/Managed/Assembly-CSharp.dll`
   (plus `mscorlib.dll`) **from the client actually installed**, not
   `libil2cpp.so`.

2. **The addresses come from that assembly, at load time.** Everything above is
   a name. `Il2CppDumper` is the wrong tool for this target;
   `dnSpy`/`ilspycmd` is the right one, and the output is a field offset table
   instead of an RVA table.

Until the client binary is on disk, this list is the work plan and nothing
more: it says what to implement and in what order, and it says which single
call - `SetAimRotation` - decides whether the account survives the week.