import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    title: qsTr("Settings")
    modal: true
    standardButtons: Dialog.Close
    anchors.centerIn: parent
    width: 480
    height: 360

    required property var controller

    onAccepted: controller.saveSettings()
    onClosed: controller.saveSettings()

    ColumnLayout {
        anchors.fill: parent

        TabBar {
            id: tabBar
            Layout.fillWidth: true
            TabButton { text: qsTr("Provider") }
            TabButton { text: qsTr("Models") }
            TabButton { text: qsTr("MCP Server") }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabBar.currentIndex

            GridLayout {
                columns: 2

                Label { text: qsTr("Provider") }
                TextField {
                    Layout.fillWidth: true
                    text: root.controller ? root.controller.provider : ""
                    onEditingFinished: root.controller.provider = text
                }

                Label { text: qsTr("Host IP") }
                TextField {
                    Layout.fillWidth: true
                    text: root.controller ? root.controller.hostIp : ""
                    onEditingFinished: root.controller.hostIp = text
                }

                Label { text: qsTr("Host Port") }
                SpinBox {
                    Layout.fillWidth: true
                    from: 1
                    to: 65535
                    value: root.controller ? root.controller.hostPort : 0
                    onValueModified: root.controller.hostPort = value
                }

                Label { text: qsTr("API Key") }
                TextField {
                    Layout.fillWidth: true
                    echoMode: TextInput.Password
                    text: root.controller ? root.controller.apiKey : ""
                    onEditingFinished: root.controller.apiKey = text
                }
            }

            GridLayout {
                columns: 2

                Label { text: qsTr("Chat Model") }
                TextField {
                    Layout.fillWidth: true
                    text: root.controller ? root.controller.chatModel : ""
                    onEditingFinished: root.controller.chatModel = text
                }

                Label { text: qsTr("Atomizer Model") }
                TextField {
                    Layout.fillWidth: true
                    text: root.controller ? root.controller.atomizerModel : ""
                    onEditingFinished: root.controller.atomizerModel = text
                }
            }

            GridLayout {
                columns: 2

                Label { text: qsTr("Host") }
                TextField {
                    Layout.fillWidth: true
                    text: root.controller ? root.controller.mcpHost : ""
                    onEditingFinished: root.controller.mcpHost = text
                }

                Label { text: qsTr("Port") }
                SpinBox {
                    Layout.fillWidth: true
                    from: 1
                    to: 65535
                    value: root.controller ? root.controller.mcpPort : 0
                    onValueModified: root.controller.mcpPort = value
                }

                Label { text: qsTr("Transport") }
                ComboBox {
                    Layout.fillWidth: true
                    model: ["stdio", "sse"]
                    currentIndex: root.controller ? model.indexOf(root.controller.mcpTransport) : -1
                    onActivated: root.controller.mcpTransport = currentValue
                }
            }
        }
    }
}
