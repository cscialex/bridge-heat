
# Bridge Heat

A Home Assistant Integration that collects environmental sensor data, with user-controlled options for secure data sharing for analysis.


## Installation

(1.) After pressing Download, restart Home Asisstant. 

(Settings &rarr; Developer's Tools &rarr; Restart)

(2.) Add the Bridge Heat Integration to your home. 

(Settings &rarr; Devices & services &rarr; Bridge Heat &rarr; Add Integration)

(3.) Review your permission choices carefully before starting the integration.

## Changing Permissions

(1.) Navigate to the Bridge Heat Integration on your device.

(Settings &rarr; Devices & services &rarr; Bridge Heat)

(2.) Change your permissions by configuring the Permissions entity.



    
## Credentials and data sharing

Obtain an API credential from the research operator and enter it during setup.
After upgrading an existing installation, use **Configure / Options** to set a
replacement credential. The formerly embedded shared key needs external revocation
and rotation. Protect Home Assistant configuration files and backups.

Only enabled temperature, humidity, pressure, and optional air-quality state changes
are shared, with entity IDs, timestamps, and measurement class/unit metadata.
Precise Home Assistant location is shared by default for research compatibility;
you can disable it in setup or Options. Entity names may disclose room names.
Recorder history is not guaranteed periodic sampling.

Read the [change briefing and migration requirements](CHANGE_BRIEFING.md) before
upgrading. In particular, coordinate the new installation identity with the research
operator. Recovery depends on Recorder retention and backend acceptance; duplicate
or missing records remain possible without backend support.

## Acknowledgements

We gratefully acknowledge Mount Sinai Health System and the Johns Hopkins Bloomberg School of Public Health for their support and contributions to this work.

![Logo](https://upload.wikimedia.org/wikipedia/en/4/47/Bloomberg.logo.small.horizontal.blue.png)
![Logo](https://www.aaos.org/globalassets/video-portal/partner-logos/mountsinai500-tr.png)
