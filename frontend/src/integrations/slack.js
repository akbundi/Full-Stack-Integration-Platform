import axios from "axios";

const HUBSPOT_API_BASE = "http://localhost:8000/hubspot";

export const authorizeHubSpot = async () => {
    try {
        window.location.href = `${HUBSPOT_API_BASE}/authorize`;
    } catch (error) {
        console.error("HubSpot authorization error:", error);
    }
};

export const fetchHubSpotCredentials = async () => {
    try {
        const response = await axios.get(`${HUBSPOT_API_BASE}/credentials`);
        return response.data;
    } catch (error) {
        console.error("Error fetching HubSpot credentials:", error);
        return null;
    }
};

export const fetchHubSpotItems = async () => {
    try {
        const response = await axios.get(`${HUBSPOT_API_BASE}/items`);
        return response.data;
    } catch (error) {
        console.error("Error fetching HubSpot items:", error);
        return [];
    }
};
