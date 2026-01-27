Here is how the logic is applied:

### 1. Right-sizing the "Fuel Tank"
The script recognizes the **1.44 kWh** capacity rather than the much larger 5.2 kWh pack from the logs. By shrinking the "fuel tank" in the code, the simulation correctly calculates that every kilometer driven takes a bigger bite out of the remaining energy, leading to a more realistic depth of discharge.

### 2. Accounting for the "Struggle" (Voltage Sag)
Smaller batteries feel the weight of a load more than large ones. It is similar to a small engine working harder to pull the same weight as a V8. The **internal resistance** values in the script are increased. This ensures the model accurately predicts "Voltage Sag"—the way the power dips when a rider hits the throttle—which is much more pronounced on a 30Ah pack.

### 3. Realistic Wear and Tear
Drawing 50 Amps from a massive battery is easy work; drawing that same 50 Amps from a 30Ah battery is a high-stress workout. Because the cells work harder (a higher **C-Rate**), they naturally age faster. The degradation logic is adjusted upward to "anticipate" the extra stress, ensuring the long-term ROI projections aren't overly optimistic.

### 4. Thinking Like a Rider (The Swap Logic)
In the original 72V data, a rider could go a long time before worrying about a charge. With the 30Ah pack, the "range anxiety" kicks in much sooner. The **Swap Threshold** is tuned so the simulation triggers a battery swap exactly when a real Nairobi Boda Boda rider would start looking for a station. This provides a much more accurate picture of the daily operating expenses (OPEX).
