import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    property string title: qsTr("Queue & Raw List")
    required property var queueModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 4

        ListView {
            id: queueListView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: queueModel
            visible: count > 0

            delegate: ItemDelegate {
                width: ListView.view.width
                text: title + "  [" + status + "]"
                Label {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text: error
                    color: "#f38ba8"
                    visible: error.length > 0
                }
            }
        }

        Label {
            Layout.alignment: Qt.AlignHCenter
            text: qsTr("Queue is empty")
            opacity: 0.6
            visible: queueListView.count === 0
        }
    }
}
